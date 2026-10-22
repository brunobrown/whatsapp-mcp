import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from dataclasses import dataclass
from typing import Callable, Optional, List, Tuple
import os.path
from pathlib import Path
import requests
import json
import audio
import localconfig
import send_policy
import logging
import re

MESSAGES_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'whatsapp-bridge', 'store', 'messages.db')
logger = logging.getLogger(__name__)


def _connect() -> sqlite3.Connection:
    """The bridge database, read-only. The server never writes it, and a missing file is an error, not a new empty database."""
    return sqlite3.connect(Path(MESSAGES_DB_PATH).resolve().as_uri() + "?mode=ro", uri=True)

WHATSAPP_API_BASE_URL = "http://localhost:8080/api"

@dataclass
class Message:
    timestamp: datetime
    sender: str
    content: str
    is_from_me: bool
    chat_jid: str
    id: str
    chat_name: Optional[str] = None
    media_type: Optional[str] = None
    filename: Optional[str] = None
    mimetype: Optional[str] = None
    file_length: Optional[int] = None
    forwarded: bool = False
    quoted_message_id: Optional[str] = None
    local_path: Optional[str] = None
    rowid: Optional[int] = None  # write order, breaks ties between messages of the same instant

@dataclass
class Chat:
    jid: str
    name: Optional[str]
    last_message_time: Optional[datetime]
    last_message: Optional[str] = None
    last_sender: Optional[str] = None
    last_is_from_me: Optional[bool] = None

    @property
    def is_group(self) -> bool:
        """Determine if chat is a group based on JID pattern."""
        return self.jid.endswith("@g.us")

@dataclass
class Contact:
    phone_number: str
    name: Optional[str]
    jid: str

@dataclass
class MessageContext:
    message: Message
    before: List[Message]
    after: List[Message]

@dataclass
class Identity:
    lid: Optional[str]
    phone: Optional[str]
    full_name: Optional[str]
    push_name: Optional[str]

    @property
    def display_name(self) -> Optional[str]:
        """The address-book name when there is one, otherwise the push name."""
        return self.full_name or self.push_name or None


_PHONE_LIKE = re.compile(r"[+\d\s().-]+", re.ASCII)


def _digits(value: str) -> str:
    """Keep the id part of a JID and only its digits: '+55 (51) 8199-3271' and '5551819932 71@s.whatsapp.net' both work."""
    ident = value.split("@")[0]
    if not _PHONE_LIKE.fullmatch(ident):
        return ""  # text such as "Teilor2024" is not a phone number
    return re.sub(r"\D", "", ident, flags=re.ASCII)


def lookup_identity(value: str) -> Optional[Identity]:
    """Find a person by LID or phone number. None when unknown or when the bridge has not created the table yet."""
    if not value:
        return None
    ident = value.split("@")[0]
    candidates = [ident] + ([_digits(value)] if _digits(value) != ident else [])
    try:
        conn = _connect()
        try:
            for candidate in candidates:
                row = conn.execute(
                    "SELECT lid, phone, full_name, push_name FROM contacts_identity WHERE lid = ? OR phone = ? LIMIT 1",
                    (candidate, candidate),
                ).fetchone()
                if row:
                    return Identity(*row)
        finally:
            conn.close()
    except sqlite3.Error:
        pass
    return None


def sender_ids(value: str) -> List[str]:
    """Every id the same person is stored under (the given one, their LID and their phone)."""
    ids = [value.split("@")[0]]
    if _digits(value) and _digits(value) not in ids:
        ids.append(_digits(value))
    identity = lookup_identity(value)
    if identity:
        for extra in (identity.lid, identity.phone):
            if extra and extra not in ids:
                ids.append(extra)
    return ids


def _person_ids_and_jids(value: str) -> Tuple[List[str], List[str]]:
    """The sender ids of a person and the JIDs their direct chats can have (phone or LID)."""
    ids = sender_ids(value)
    jids = [value]
    for ident in ids:
        for jid in (f"{ident}@s.whatsapp.net", f"{ident}@lid"):
            if jid not in jids:
                jids.append(jid)
    return ids, jids


def get_sender_name(sender_jid: str) -> str:
    identity = lookup_identity(sender_jid)
    if identity and identity.display_name:
        return identity.display_name
    try:
        conn = _connect()
        cursor = conn.cursor()
        
        # First try matching by exact JID
        cursor.execute("""
            SELECT name
            FROM chats
            WHERE jid = ?
            LIMIT 1
        """, (sender_jid,))
        
        result = cursor.fetchone()
        
        # If no result, try looking for the number within JIDs
        if not result:
            # Extract the phone number part if it's a JID
            if '@' in sender_jid:
                phone_part = sender_jid.split('@')[0]
            else:
                phone_part = sender_jid
                
            cursor.execute("""
                SELECT name
                FROM chats
                WHERE jid LIKE ?
                LIMIT 1
            """, (f"%{phone_part}%",))
            
            result = cursor.fetchone()
        
        if result and result[0]:
            return result[0]
        else:
            return sender_jid
        
    except sqlite3.Error as e:
        logger.error("Database error while getting sender name: %s", e)
        return sender_jid
    finally:
        if 'conn' in locals():
            conn.close()


def sender_label(sender: str) -> str:
    """'Name (id)' when the sender's name is known, otherwise just the id."""
    name = get_sender_name(sender)
    if name and name != sender:
        return f"{name} ({sender})"
    return sender


_MENTION = re.compile(r"(?<![\w@])@(\d{5,20})\b")


def resolve_mentions(content: str) -> str:
    """Replace @<digits> of known people with @Name. Unknown ids and other text stay as they are."""
    def replace(match: "re.Match[str]") -> str:
        identity = lookup_identity(match.group(1))
        name = identity.display_name if identity else None
        # a push name can itself start with "@" (a username), which would read "@@name"
        return f"@{name.lstrip('@')}" if name else match.group(0)
    return _MENTION.sub(replace, content or "")

# Columns the bridge added over time; a database from an older bridge may lack them.
_OPTIONAL_MESSAGE_COLUMNS = ("filename", "mimetype", "file_length", "forwarded", "quoted_id", "local_path")


def _message_select(cursor: sqlite3.Cursor) -> str:
    """SELECT list for Message rows. Columns missing from the database read as NULL."""
    have = {row[1] for row in cursor.execute("PRAGMA table_info(messages)")}
    optional = ", ".join(f"messages.{c}" if c in have else f"NULL AS {c}" for c in _OPTIONAL_MESSAGE_COLUMNS)
    return (
        "messages.timestamp, messages.sender, chats.name, messages.content, messages.is_from_me, "
        f"chats.jid, messages.id, messages.media_type, {optional}, messages.rowid"
    )


def _message_from_row(row) -> Message:
    return Message(
        timestamp=datetime.fromisoformat(row[0]),
        sender=row[1],
        chat_name=row[2],
        content=row[3],
        is_from_me=row[4],
        chat_jid=row[5],
        id=row[6],
        media_type=row[7],
        filename=row[8],
        file_length=row[10],
        mimetype=row[9],
        forwarded=bool(row[11]),
        quoted_message_id=row[12] or None,
        local_path=row[13],
        rowid=row[14],
    )


def format_size(size: Optional[int]) -> str:
    """Human readable file size; empty when the size is unknown."""
    if not size or size < 0:
        return ""
    if size < 1024:
        return f"{size} B"
    for unit in ("KB", "MB", "GB"):
        size /= 1024
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"


def _file_details(message: Message) -> str:
    """` - File: name (mime, size)` for a message with media, or ''."""
    details = ", ".join(part for part in (message.mimetype, format_size(message.file_length)) if part)
    if message.filename:
        return f" - File: {message.filename}" + (f" ({details})" if details else "")
    return f" ({details})" if details else ""


def _message_prefix(message: Message) -> str:
    """The bracketed prefix of a message: media type and IDs, then forwarded, reply and local path."""
    notes = []
    if message.forwarded:
        notes.append("Forwarded")
    if message.quoted_message_id:
        notes.append(f"Reply to: {message.quoted_message_id}")
    if message.media_type:
        # a file that was deleted (retention) is not offered
        if message.local_path and os.path.isfile(message.local_path):
            notes.append(f"Local path: {message.local_path}")
        extra = "".join(f" - {note}" for note in notes)
        return f"[{message.media_type} - Message ID: {message.id} - Chat JID: {message.chat_jid}{_file_details(message)}{extra}] "
    return f"[{' - '.join(notes)}] " if notes else ""


def format_message(message: Message, show_chat_info: bool = True, marked: bool = False) -> str:
    """One line for a message. A found message (marked) starts with '>> '."""
    output = ">> " if marked else ""
    
    if show_chat_info and message.chat_name:
        output += f"[{message.timestamp:%Y-%m-%d %H:%M:%S}] Chat: {message.chat_name} "
    else:
        output += f"[{message.timestamp:%Y-%m-%d %H:%M:%S}] "
        
    content_prefix = _message_prefix(message)
    
    try:
        sender_name = sender_label(message.sender) if not message.is_from_me else "Me"
        output += f"From: {sender_name}: {content_prefix}{resolve_mentions(message.content)}\n"
    except Exception as e:
        logger.error("Error formatting message: %s", e)
    return output

def format_messages_list(messages: List[Message], show_chat_info: bool = True) -> str:
    output = ""
    if not messages:
        output += "No messages to display."
        return output
    
    for message in messages:
        output += format_message(message, show_chat_info)
    return output


# Stored times carry the offset they were written with, so they are compared as instants
# (milliseconds, UTC), never as text. SQLite reads a time without offset as UTC.
_INSTANT = "strftime('%Y-%m-%d %H:%M:%f', messages.timestamp)"
_FORMATS = ("text", "structured")
_ORDERS = ("asc", "desc")


def _default_zone():
    """The zone for a date given without one: WHATSAPP_TIMEZONE, or None for the system zone."""
    name = os.environ.get("WHATSAPP_TIMEZONE")
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"WHATSAPP_TIMEZONE '{name}' is not a known time zone (use an IANA name such as America/Recife)")


def _instant_param(value: str, label: str) -> str:
    """ISO-8601 text (with or without offset) as the UTC millisecond text that _INSTANT yields."""
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"Invalid date format for '{label}': {value}. Please use ISO-8601 format.")
    if moment.tzinfo is None:
        zone = _default_zone()
        moment = moment.replace(tzinfo=zone) if zone else moment.astimezone()
    moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%d %H:%M:%S.") + f"{moment.microsecond // 1000:03d}"


def _aware(moment: datetime) -> datetime:
    """The stored time with its offset; one stored without is UTC, as SQLite reads it."""
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _message_dict(message: Message, is_match: bool) -> dict:
    media = None
    if message.media_type:
        local = message.local_path if message.local_path and os.path.isfile(message.local_path) else None
        media = {
            "type": message.media_type,
            "filename": message.filename,
            "mimetype": message.mimetype,
            "size": message.file_length,
            "forwarded": message.forwarded,
            "quoted_message_id": message.quoted_message_id,
            "local_path": local,
        }
    return {
        "id": message.id,
        "timestamp": _aware(message.timestamp).isoformat(),
        "chat_jid": message.chat_jid,
        "chat_name": message.chat_name,
        "sender": message.sender,
        "sender_name": "Me" if message.is_from_me else sender_label(message.sender),
        "content": resolve_mentions(message.content),
        "is_from_me": bool(message.is_from_me),
        "is_match": is_match,
        "media": media,
    }


def _neighbours(cursor: sqlite3.Cursor, columns: str, target: tuple, count: int, earlier: bool) -> List[Message]:
    """Messages of the target's chat before (earlier) or after it by instant, then write order; chronological."""
    chat_jid, instant, rowid = target
    if count <= 0:
        return []
    sign, direction = ("<", "DESC") if earlier else (">", "ASC")
    cursor.execute(f"""
        SELECT {columns} FROM messages JOIN chats ON messages.chat_jid = chats.jid
        WHERE messages.chat_jid = ? AND ({_INSTANT}, messages.rowid) {sign} (?, ?)
        ORDER BY {_INSTANT} {direction}, messages.rowid {direction} LIMIT ?
    """, (chat_jid, instant, rowid, count))
    found = [_message_from_row(row) for row in cursor.fetchall()]
    return found[::-1] if earlier else found


def _target_of(cursor: sqlite3.Cursor, message: Message) -> Optional[tuple]:
    cursor.execute(f"SELECT {_INSTANT} FROM messages WHERE messages.rowid = ?", (message.rowid,))
    row = cursor.fetchone()
    return (message.chat_jid, row[0], message.rowid) if row else None


def _with_context(cursor: sqlite3.Cursor, columns: str, found: List[Message], before: int, after: int):
    """(message, is_match) for the found messages and their neighbours: one group per chat, chronological, no repeats."""
    groups: dict = {}
    for message in found:
        group = groups.setdefault(message.chat_jid, {})
        group[message.id] = (message, True)
    for message in found:
        target = _target_of(cursor, message)
        if not target:
            continue
        for neighbour in _neighbours(cursor, columns, target, before, True) + _neighbours(cursor, columns, target, after, False):
            groups[message.chat_jid].setdefault(neighbour.id, (neighbour, False))
    ordered = []
    for group in groups.values():  # chats in the order their first match came
        ordered.append(sorted(group.values(), key=lambda pair: (_aware(pair[0].timestamp), pair[0].rowid)))
    return ordered


def list_messages(
    after: Optional[str] = None,
    before: Optional[str] = None,
    sender_phone_number: Optional[str] = None,
    chat_jid: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_context: bool = True,
    context_before: int = 1,
    context_after: int = 1,
    format: str = "text",
    order: str = "desc",
    chat_name: Optional[str] = None
):
    """Get messages matching the specified criteria with optional context.

    Text (default) or, with format="structured", a dict of messages, total, page, limit, has_more, next_page.
    """
    if format not in _FORMATS:
        raise ValueError(f"Invalid format: {format}. Use one of: {', '.join(_FORMATS)}.")
    if order not in _ORDERS:
        raise ValueError(f"Invalid order: {order}. Use one of: {', '.join(_ORDERS)}.")
    if limit < 1 or page < 0:
        raise ValueError("limit must be at least 1 and page at least 0.")
    try:
        conn = _connect()
        conn.create_function("casefold", 1, lambda v: v.casefold() if isinstance(v, str) else v)
        cursor = conn.cursor()
        columns = _message_select(cursor)
        
        where_clauses = []
        params = []
        
        # Add filters
        if after:
            where_clauses.append(f"{_INSTANT} > ?")
            params.append(_instant_param(after, "after"))

        if before:
            where_clauses.append(f"{_INSTANT} < ?")
            params.append(_instant_param(before, "before"))

        if sender_phone_number:
            ids = sender_ids(sender_phone_number)
            where_clauses.append(f"messages.sender IN ({','.join('?' * len(ids))})")
            params.extend(ids)
            
        if chat_jid:
            where_clauses.append("messages.chat_jid = ?")
            params.append(chat_jid)

        if chat_name:
            where_clauses.append("casefold(chats.name) LIKE ? ESCAPE '\\'")
            escaped = chat_name.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(f"%{escaped}%")
            
        if query:
            where_clauses.append("LOWER(messages.content) LIKE LOWER(?)")
            params.append(f"%{query}%")

        where = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        source = "FROM messages JOIN chats ON messages.chat_jid = chats.jid" + where

        cursor.execute(f"SELECT COUNT(*) {source}", tuple(params))
        total = cursor.fetchone()[0]

        offset = page * limit
        direction = order.upper()
        cursor.execute(
            f"SELECT {columns} {source} ORDER BY {_INSTANT} {direction}, messages.rowid {direction} LIMIT ? OFFSET ?",
            tuple(params) + (limit, offset))
        result = [_message_from_row(row) for row in cursor.fetchall()]

        if include_context and result:
            groups = _with_context(cursor, columns, result, context_before, context_after)
        else:
            groups = [[(message, True) for message in result]] if result else []

        if format == "text":
            if not groups:
                return format_messages_list([])
            return "\n".join(
                "".join(format_message(m, True, marked=include_context and is_match) for m, is_match in group)
                for group in groups)

        has_more = offset + len(result) < total
        return {
            "messages": [_message_dict(m, is_match) for group in groups for m, is_match in group],
            "total": total,
            "page": page,
            "limit": limit,
            "has_more": has_more,
            "next_page": page + 1 if has_more else None,
        }
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return [] if format == "text" else {"messages": [], "total": 0, "page": page, "limit": limit,
                                            "has_more": False, "next_page": None, "error": str(e)}
    finally:
        if 'conn' in locals():
            conn.close()


def get_message_context(
    message_id: str,
    before: int = 5,
    after: int = 5,
    chat_jid: Optional[str] = None
) -> MessageContext:
    """Get context around a specific message; chat_jid picks the chat when the ID exists in several.

    Both sides are in chronological order.
    """
    try:
        conn = _connect()
        cursor = conn.cursor()
        columns = _message_select(cursor)
        
        # Get the target message first
        scope, scope_params = ("AND messages.chat_jid = ?", (chat_jid,)) if chat_jid else ("", ())
        cursor.execute(f"""
            SELECT {columns}, {_INSTANT}
            FROM messages
            JOIN chats ON messages.chat_jid = chats.jid
            WHERE messages.id = ? {scope}
            ORDER BY {_INSTANT} DESC, messages.rowid DESC
        """, (message_id,) + scope_params)
        msg_data = cursor.fetchone()
        
        if not msg_data:
            raise ValueError(f"Message with ID {message_id} not found")
            
        target_message = _message_from_row(msg_data)
        target = (target_message.chat_jid, msg_data[-1], target_message.rowid)
        
        return MessageContext(
            message=target_message,
            before=_neighbours(cursor, columns, target, before, True),
            after=_neighbours(cursor, columns, target, after, False)
        )
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        raise
    finally:
        if 'conn' in locals():
            conn.close()


# The latest message of a chat comes from `messages`, never from matching chats.last_message_time:
# that time also moves for events that were not stored as messages, and used to move backwards,
# so an equality join left last_message empty. Instants are compared with datetime() because the
# stored times can carry different UTC offsets.
_LAST_MESSAGE_JOIN = """
    LEFT JOIN messages lm ON lm.rowid = (
        SELECT m.rowid FROM messages m WHERE m.chat_jid = chats.jid
        ORDER BY datetime(m.timestamp) DESC, m.rowid DESC LIMIT 1
    )
"""
_CHAT_COLUMNS = """
    chats.jid, chats.name, COALESCE(lm.timestamp, chats.last_message_time),
    lm.content, lm.sender, lm.is_from_me, lm.media_type
"""
_LAST_ACTIVE = "datetime(COALESCE(lm.timestamp, chats.last_message_time))"


def _last_message_text(content: Optional[str], media_type: Optional[str]) -> Optional[str]:
    """The text of a chat's last message; media shows as '[type] caption', or '[type]' without one."""
    if media_type:
        return f"[{media_type}] {content}".strip() if content else f"[{media_type}]"
    return content


def _chat_from_row(row, include_last_message: bool = True) -> Chat:
    jid, name, last_time, content, sender, is_from_me, media_type = row
    chat = Chat(
        jid=jid,
        name=name,
        last_message_time=datetime.fromisoformat(last_time) if last_time else None,
    )
    if include_last_message:
        chat.last_message = _last_message_text(content, media_type)
        chat.last_sender = sender
        chat.last_is_from_me = is_from_me
    return chat


def list_chats(
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_last_message: bool = True,
    sort_by: str = "last_active"
) -> List[Chat]:
    """Get chats matching the specified criteria."""
    try:
        conn = _connect()
        cursor = conn.cursor()

        where, params = "", []
        if query:
            where = "WHERE (LOWER(chats.name) LIKE LOWER(?) OR chats.jid LIKE ?)"
            params.extend([f"%{query}%", f"%{query}%"])
        order_by = f"{_LAST_ACTIVE} DESC" if sort_by == "last_active" else "chats.name"

        cursor.execute(
            f"SELECT {_CHAT_COLUMNS} FROM chats {_LAST_MESSAGE_JOIN} {where} ORDER BY {order_by} LIMIT ? OFFSET ?",
            (*params, limit, page * limit),
        )
        return [_chat_from_row(row, include_last_message) for row in cursor.fetchall()]

    except sqlite3.Error as e:
        logger.error("Database error: %s", e)
        return []
    finally:
        if 'conn' in locals():
            conn.close()


def _search_identities(conn: sqlite3.Connection, query: str) -> Tuple[List[Contact], set]:
    """Known people whose name, phone or LID matches, and every id they are stored under.

    Both are empty when the bridge has not created contacts_identity yet."""
    pattern = f"%{query.lower()}%"
    digits = _digits(query)
    # a query of fewer than 3 digits is matched against names only, never against ids
    digit_pattern = f"%{digits}%" if len(digits) >= 3 else None
    try:
        rows = conn.execute("""
            SELECT lid, phone, full_name, push_name
            FROM contacts_identity
            WHERE LOWER(full_name) LIKE ? OR LOWER(push_name) LIKE ?
                OR lid LIKE ? OR phone LIKE ?
            ORDER BY COALESCE(full_name, push_name), phone, lid
            LIMIT 50
        """, (pattern, pattern, digit_pattern, digit_pattern)).fetchall()
    except sqlite3.Error:
        return [], set()
    people, ids = [], set()
    for lid, phone, full_name, push_name in rows:
        jid = f"{phone}@s.whatsapp.net" if phone else f"{lid}@lid"
        people.append(Contact(phone_number=phone or lid, name=full_name or push_name, jid=jid))
        ids.update(i for i in (lid, phone) if i)
    return people, ids


def search_contacts(query: str) -> List[Contact]:
    """Search contacts by name, phone number or LID."""
    try:
        conn = _connect()
        cursor = conn.cursor()

        result, known_ids = _search_identities(conn, query)

        search_pattern = '%' +query + '%'
        
        cursor.execute("""
            SELECT DISTINCT 
                jid,
                name
            FROM chats
            WHERE 
                (LOWER(name) LIKE LOWER(?) OR LOWER(jid) LIKE LOWER(?))
                AND jid NOT LIKE '%@g.us'
            ORDER BY name, jid
            LIMIT 50
        """, (search_pattern, search_pattern))
        
        for jid, name in cursor.fetchall():
            user = jid.split('@')[0]
            if user in known_ids:
                continue  # the same person is already listed with their name
            result.append(Contact(phone_number=user, name=name, jid=jid))
            
        return result[:50]
        
    except sqlite3.Error as e:
        logger.error("Database error: %s", e)
        return []
    finally:
        if 'conn' in locals():
            conn.close()


def get_contact_chats(jid: str, limit: int = 20, page: int = 0) -> List[Chat]:
    """Get all chats involving the contact, each once.
    
    Args:
        jid: The contact's JID to search for
        limit: Maximum number of chats to return (default 20)
        page: Page number for pagination (default 0)
    """
    try:
        ids, jids = _person_ids_and_jids(jid)
        conn = _connect()
        cursor = conn.cursor()

        cursor.execute(
            f"""
            SELECT {_CHAT_COLUMNS}
            FROM chats {_LAST_MESSAGE_JOIN}
            WHERE chats.jid IN ({",".join("?" * len(jids))})
                OR EXISTS (
                    SELECT 1 FROM messages x
                    WHERE x.chat_jid = chats.jid AND x.sender IN ({",".join("?" * len(ids))})
                )
            ORDER BY {_LAST_ACTIVE} DESC
            LIMIT ? OFFSET ?
            """,
            (*jids, *ids, limit, page * limit),
        )
        return [_chat_from_row(row) for row in cursor.fetchall()]

    except sqlite3.Error as e:
        logger.error("Database error: %s", e)
        return []
    finally:
        if 'conn' in locals():
            conn.close()


def get_last_interaction(jid: str) -> str:
    """Get most recent message involving the contact."""
    try:
        ids, jids = _person_ids_and_jids(jid)
        conn = _connect()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                m.timestamp,
                m.sender,
                c.name,
                m.content,
                m.is_from_me,
                c.jid,
                m.id,
                m.media_type
            FROM messages m
            JOIN chats c ON m.chat_jid = c.jid
            WHERE m.sender IN ({sender_marks}) OR c.jid IN ({jid_marks})
            ORDER BY m.timestamp DESC
            LIMIT 1
        """.format(sender_marks=",".join("?" * len(ids)), jid_marks=",".join("?" * len(jids))),
            (*ids, *jids))
        
        msg_data = cursor.fetchone()
        
        if not msg_data:
            return None
            
        message = Message(
            timestamp=datetime.fromisoformat(msg_data[0]),
            sender=msg_data[1],
            chat_name=msg_data[2],
            content=msg_data[3],
            is_from_me=msg_data[4],
            chat_jid=msg_data[5],
            id=msg_data[6],
            media_type=msg_data[7]
        )
        
        return format_message(message)
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()


def get_chat(chat_jid: str, include_last_message: bool = True) -> Optional[Chat]:
    """Get chat metadata by JID."""
    try:
        conn = _connect()
        cursor = conn.cursor()

        cursor.execute(
            f"SELECT {_CHAT_COLUMNS} FROM chats {_LAST_MESSAGE_JOIN} WHERE chats.jid = ?",
            (chat_jid,),
        )
        row = cursor.fetchone()
        return _chat_from_row(row, include_last_message) if row else None

    except sqlite3.Error as e:
        logger.error("Database error: %s", e)
        return None
    finally:
        if 'conn' in locals():
            conn.close()


def get_direct_chat_by_contact(sender_phone_number: str) -> Optional[Chat]:
    """Get chat metadata by sender phone number."""
    try:
        conn = _connect()
        cursor = conn.cursor()

        cursor.execute(
            f"""
            SELECT {_CHAT_COLUMNS}
            FROM chats {_LAST_MESSAGE_JOIN}
            WHERE chats.jid LIKE ? AND chats.jid NOT LIKE '%@g.us'
            LIMIT 1
            """,
            (f"%{sender_phone_number}%",),
        )
        row = cursor.fetchone()
        return _chat_from_row(row) if row else None

    except sqlite3.Error as e:
        logger.error("Database error: %s", e)
        return None
    finally:
        if 'conn' in locals():
            conn.close()

@dataclass
class SendResult:
    success: bool
    message: str
    message_id: Optional[str] = None
    timestamp: Optional[str] = None
    chat_jid: Optional[str] = None
    error_code: Optional[str] = None
    preview: Optional[dict] = None


def _refused(response) -> bool:
    """True when the bridge turned the request down for its token, Host or Origin (401/403)."""
    return response.status_code in (401, 403)


def _post_send(payload: dict) -> SendResult:
    """POST a send request to the bridge and parse its answer."""
    try:
        response = requests.post(f"{WHATSAPP_API_BASE_URL}/send", json=payload, headers=localconfig.auth_headers())
    except requests.RequestException as e:
        return SendResult(False, f"Request error: {str(e)}")
    except Exception as e:
        return SendResult(False, f"Unexpected error: {str(e)}")

    if _refused(response):
        return SendResult(False, localconfig.UNAUTHORIZED_HINT, error_code=localconfig.BRIDGE_UNAUTHORIZED)
    if response.status_code != 200:
        return SendResult(False, f"Error: HTTP {response.status_code} - {response.text}")
    try:
        result = response.json()
    except ValueError:
        return SendResult(False, f"Error parsing response: {response.text}")

    success = result.get("success", False)
    message = result.get("message", "Unknown response")
    if not success:
        return SendResult(False, message)
    return SendResult(
        True,
        message,
        message_id=result.get("message_id"),
        timestamp=result.get("timestamp"),
        chat_jid=result.get("chat_jid"),
    )


def _open_chats() -> sqlite3.Connection:
    """Open the bridge database read-only (never creating it) and check that chats can be read."""
    try:
        conn = sqlite3.connect(Path(MESSAGES_DB_PATH).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            conn.execute("SELECT 1 FROM chats LIMIT 1")
        except sqlite3.Error:
            conn.close()
            raise
        return conn
    except sqlite3.Error as e:
        raise send_policy.DirectoryError(str(e)) from e


def _chat_jids_by_name(name: str) -> List[str]:
    """JIDs of the chats named exactly `name`, ignoring case. Raises DirectoryError when chats are unreadable."""
    wanted = name.strip().casefold()
    conn = _open_chats()
    try:
        rows = conn.execute("SELECT jid, name FROM chats WHERE name IS NOT NULL").fetchall()
    except sqlite3.Error as e:
        raise send_policy.DirectoryError(str(e)) from e
    finally:
        conn.close()
    return [jid for jid, chat_name in rows if wanted and chat_name.strip().casefold() == wanted]


def _chat_name_of(jid: str) -> Optional[str]:
    """Name of the chat of a JID, trying the other ids of the same person (phone/LID) for direct chats.
    Only used to word messages and to match name rules; an unreadable database gives None."""
    candidates = [jid]
    if not jid.endswith("@g.us"):
        candidates += _person_ids_and_jids(jid)[1]
    try:
        conn = _open_chats()
    except send_policy.DirectoryError:
        return None
    try:
        for candidate in candidates:
            row = conn.execute("SELECT name FROM chats WHERE jid = ?", (candidate,)).fetchone()
            if row and row[0]:
                return row[0]
    except sqlite3.Error as e:
        logger.error("Database error: %s", e)
    finally:
        conn.close()
    return None


def _directory_ready() -> None:
    _open_chats().close()


SEND_DIRECTORY = send_policy.Directory(
    jids_by_name=_chat_jids_by_name,
    name_of=_chat_name_of,
    ids_of=sender_ids,
    is_phone=lambda text: bool(_digits(text)),
    ready=_directory_ready,
)


def _guarded_send(kind: str, recipient: str, size: int, override_token: Optional[str],
                  send: "Callable[[send_policy.Target], SendResult]",
                  preview: "Optional[Callable[[send_policy.Target], SendResult]]" = None) -> SendResult:
    """Resolve the destination, apply the send policy, then send (or only preview) and audit the attempt."""
    dry_run = preview is not None  # a dry run sends nothing, so nothing of it is audited
    target = send_policy.resolve_target(recipient, SEND_DIRECTORY)
    if not target.ok:
        if not dry_run:
            send_policy.audit(kind, recipient, None, size, "failed", rule=target.error_code)
        return SendResult(False, target.message, error_code=target.error_code)

    decision = send_policy.evaluate(target, override_token, SEND_DIRECTORY)
    if not decision.allowed:
        if not dry_run:
            send_policy.audit(kind, recipient, target, size, "blocked", rule=decision.rule)
        return SendResult(False, decision.message, error_code=send_policy.BLOCKED)

    if preview is not None:
        return preview(target)

    result = send(target)
    send_policy.audit(kind, recipient, target, size, "sent" if result.success else "failed",
                      rule=decision.rule, override=decision.override, message_id=result.message_id)
    return result


@dataclass
class Mention:
    token: str   # the digits written after @ in the text
    jid: str


# @+55518175-1110 (digits with . - ( ) after the +), @555181751110 or @<lid> (plain digits); no spaces.
# Not after a word character, @, / or ` so e-mails, URLs and inline code are left alone, and a plain
# @2024-10-21 is not a mention either.
_MENTION_TOKEN = re.compile(r"(?<![\w@/`])@(?:\+(\d[\d().\-]*\d)|(\d+))(?!\d)")
_MAX_MENTIONS = 50
_MIN_MENTION_DIGITS, _MAX_MENTION_DIGITS = 7, 20
_MAX_PHONE_DIGITS = 13  # longer ids are LIDs, the same rule as the bridge


def _mention_jid(digits: str) -> str:
    return f"{digits}@{'s.whatsapp.net' if len(digits) <= _MAX_PHONE_DIGITS else 'lid'}"


def convert_mentions(text: str, explicit: Optional[List[str]] = None) -> Tuple[str, List[Mention]]:
    """Turn @+55… / @<phone> / @<lid> tokens into `@<digits>` plus the people to tag.

    The rest of the text is left alone. `explicit` adds people by JID, phone or LID. Raises ValueError
    for an explicit mention that is none of those.
    """
    mentions: List[Mention] = []

    def add(digits: str, jid: str) -> None:
        if all(m.jid != jid for m in mentions):
            mentions.append(Mention(digits, jid))

    def replace(match: "re.Match[str]") -> str:
        digits = re.sub(r"\D", "", match.group(1) or match.group(2), flags=re.ASCII)
        if not _MIN_MENTION_DIGITS <= len(digits) <= _MAX_MENTION_DIGITS:
            return match.group(0)
        add(digits, _mention_jid(digits))
        return f"@{digits}"

    text = _MENTION_TOKEN.sub(replace, text or "")

    for entry in explicit or []:
        if "@" in entry:
            jid = send_policy.canonical_jid(entry)
            if jid is None or jid.endswith("@g.us"):
                raise ValueError(f"Invalid mention: {entry!r}")
            add(jid.split("@")[0], jid)
            continue
        digits = _digits(entry)
        if not _MIN_MENTION_DIGITS <= len(digits) <= _MAX_MENTION_DIGITS:
            raise ValueError(f"Invalid mention: {entry!r}")
        add(digits, _mention_jid(digits))
    if len(mentions) > _MAX_MENTIONS:
        raise ValueError(f"Too many mentions: {len(mentions)} (at most {_MAX_MENTIONS} per message)")
    return text, mentions


def _find_reply_target(message_id: str, chat_jid: str) -> Optional[dict]:
    """The stored message `message_id` of the chat, as {id, sender, text}; direct chats may be stored
    under the other addressing of the same person."""
    candidates = [chat_jid] if chat_jid.endswith("@g.us") else _person_ids_and_jids(chat_jid)[1]
    try:
        conn = sqlite3.connect(Path(MESSAGES_DB_PATH).resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error as e:
        logger.error("Database error: %s", e)
        return None
    try:
        for candidate in candidates:
            row = conn.execute("SELECT id, sender, content FROM messages WHERE id = ? AND chat_jid = ?",
                               (message_id, candidate)).fetchone()
            if row:
                return {"id": row[0], "sender": row[1], "text": row[2] or ""}
    except sqlite3.Error as e:
        logger.error("Database error: %s", e)
    finally:
        conn.close()
    return None


def send_message(recipient: str, message: str, override_token: Optional[str] = None,
                 mentions: Optional[List[str]] = None, reply_to: Optional[str] = None,
                 dry_run: bool = False) -> SendResult:
    if not recipient:
        return SendResult(False, "Recipient must be provided")
    try:
        text, tagged = convert_mentions(message or "", mentions)
    except ValueError as e:
        return SendResult(False, str(e), error_code="INVALID_MENTION")

    def reply_target(target: send_policy.Target) -> "Tuple[Optional[dict], Optional[SendResult]]":
        if not reply_to:
            return None, None
        found = _find_reply_target(reply_to, target.jid)
        if found is None:
            return None, SendResult(False, f"Message {reply_to} was not found in {target.jid}. Nothing was sent.",
                                    error_code="REPLY_TARGET_NOT_FOUND")
        return found, None

    def send(target: send_policy.Target) -> SendResult:
        _, error = reply_target(target)
        if error:
            return error
        payload = {"recipient": target.recipient, "message": text}
        if tagged:
            payload["mentions"] = [m.jid for m in tagged]
        if reply_to:
            payload["reply_to"] = reply_to
        return _post_send(payload)

    def preview(target: send_policy.Target) -> SendResult:
        found, error = reply_target(target)
        if error:
            return error
        return SendResult(True, "Dry run: nothing was sent", preview={
            "text": text,
            "chat_jid": target.jid,
            "chat_name": target.chat_name,
            "mentions": [{"token": m.token, "jid": m.jid} for m in tagged],
            "reply_to": found,
        })

    return _guarded_send("message", recipient, len(text), override_token, send, preview if dry_run else None)


def send_file(recipient: str, media_path: str, override_token: Optional[str] = None) -> SendResult:
    if not recipient:
        return SendResult(False, "Recipient must be provided")
    if not media_path:
        return SendResult(False, "Media path must be provided")
    if not os.path.isfile(media_path):
        return SendResult(False, f"Media file not found: {media_path}")
    return _guarded_send("file", recipient, os.path.getsize(media_path), override_token,
                         lambda target: _post_send({"recipient": target.recipient, "media_path": media_path}))


def send_audio_message(recipient: str, media_path: str, override_token: Optional[str] = None) -> SendResult:
    if not recipient:
        return SendResult(False, "Recipient must be provided")
    if not media_path:
        return SendResult(False, "Media path must be provided")
    if not os.path.isfile(media_path):
        return SendResult(False, f"Media file not found: {media_path}")

    def send(target: send_policy.Target) -> SendResult:
        path = media_path
        if not path.endswith(".ogg"):
            try:
                path = audio.convert_to_opus_ogg_temp(path)
            except Exception as e:
                return SendResult(False, f"Error converting file to opus ogg. You likely need to install ffmpeg: {str(e)}")
        return _post_send({"recipient": target.recipient, "media_path": path})

    return _guarded_send("audio", recipient, os.path.getsize(media_path), override_token, send)


@dataclass
class GroupParticipantsResult:
    success: bool
    message: Optional[str] = None
    participants: Optional[List[dict]] = None
    error_code: Optional[str] = None


def get_group_participants(chat_jid: str) -> GroupParticipantsResult:
    """List a group's participants (name, phone, LID, is_admin) through the bridge."""
    if not chat_jid:
        return GroupParticipantsResult(False, "chat_jid must be provided")
    try:
        response = requests.post(f"{WHATSAPP_API_BASE_URL}/group_participants", json={"chat_jid": chat_jid},
                                 headers=localconfig.auth_headers())
    except requests.RequestException as e:
        return GroupParticipantsResult(False, f"Could not reach the WhatsApp bridge: {e}")
    if _refused(response):
        return GroupParticipantsResult(False, localconfig.UNAUTHORIZED_HINT, error_code=localconfig.BRIDGE_UNAUTHORIZED)
    try:
        body = response.json()
    except ValueError:
        return GroupParticipantsResult(False, f"HTTP {response.status_code} - {response.text}")
    if response.status_code == 200 and body.get("success", False):
        return GroupParticipantsResult(True, participants=body.get("participants") or [])
    return GroupParticipantsResult(False, body.get("message") or f"HTTP {response.status_code}")


LINK_EXPIRED_HINT = (
    "O link da mídia expirou e o celular não respondeu ao pedido de reenvio. "
    "Abra o WhatsApp no celular conectado ao bridge, desbloqueado e online, e tente de novo."
)


@dataclass
class DownloadResult:
    success: bool
    message: str
    path: Optional[str] = None
    error_code: Optional[str] = None
    hint: Optional[str] = None


def _classify_download_error(message: str) -> str:
    lowered = message.lower()
    if "status code 403" in lowered and "timed out waiting for media retry" in lowered:
        return "LINK_EXPIRED_PHONE_OFFLINE"
    if "not a media message" in lowered or "failed to find message" in lowered:
        return "MEDIA_NOT_FOUND"
    return "UNKNOWN"


def _download_failure(message: str, error_code: Optional[str] = None) -> DownloadResult:
    code = error_code or _classify_download_error(message)
    hint = {"LINK_EXPIRED_PHONE_OFFLINE": LINK_EXPIRED_HINT, localconfig.BRIDGE_UNAUTHORIZED: localconfig.UNAUTHORIZED_HINT}.get(code)
    logger.error("download_media failed (%s): %s", code, message)
    return DownloadResult(False, message, error_code=code, hint=hint)


def download_media(message_id: str, chat_jid: str, timeout: Optional[float] = None) -> DownloadResult:
    """Download media from a message through the bridge.

    Args:
        message_id: The ID of the message containing the media
        chat_jid: The JID of the chat containing the message
        timeout: Seconds to wait for the bridge; no limit when omitted

    Returns:
        A DownloadResult with the local path on success, or the bridge's real
        error message and a stable error_code on failure.
    """
    extra = {} if timeout is None else {"timeout": timeout}
    try:
        response = requests.post(
            f"{WHATSAPP_API_BASE_URL}/download",
            json={"message_id": message_id, "chat_jid": chat_jid},
            headers=localconfig.auth_headers(),
            **extra,
        )
    except requests.RequestException as e:
        # a wait that ran out (not a refused or unreachable connection) is told apart only when a limit was set
        if timeout is not None and isinstance(e, requests.Timeout) and not isinstance(e, requests.ConnectTimeout):
            return _download_failure(f"The bridge did not answer within {timeout:g} seconds: {e}", "DOWNLOAD_TIMEOUT")
        return _download_failure(f"Could not reach the WhatsApp bridge: {e}", "BRIDGE_UNREACHABLE")

    if _refused(response):
        return _download_failure(localconfig.UNAUTHORIZED_HINT, localconfig.BRIDGE_UNAUTHORIZED)
    try:
        result = response.json()
    except ValueError:
        return _download_failure(f"HTTP {response.status_code} - {response.text}")

    if response.status_code == 200 and result.get("success", False):
        return DownloadResult(True, "Media downloaded successfully", path=result.get("path"))
    return _download_failure(result.get("message") or f"HTTP {response.status_code}")


BRIDGE_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'whatsapp-bridge', 'store', 'bridge.log')
_STATUS_TIMEOUT_SECONDS = 5


def _last_message_at() -> Optional[str]:
    """The newest stored message time, or None when there is none or no database."""
    try:
        conn = _open_chats()
    except (sqlite3.Error, send_policy.DirectoryError):
        return None
    try:
        row = conn.execute("SELECT timestamp FROM messages ORDER BY strftime('%Y-%m-%d %H:%M:%f', timestamp) DESC LIMIT 1").fetchone()
        return datetime.fromisoformat(row[0]).isoformat() if row and row[0] else None
    except (sqlite3.Error, ValueError):
        return None
    finally:
        conn.close()


def _last_log_at() -> Optional[str]:
    """When the bridge log was last written (the bridge's last sign of life), or None."""
    try:
        return datetime.fromtimestamp(os.path.getmtime(BRIDGE_LOG_PATH)).astimezone().isoformat()
    except OSError:
        return None


def get_connection_status() -> dict:
    """The bridge's status; a bridge that does not answer is reported as not connected."""
    try:
        response = requests.get(f"{WHATSAPP_API_BASE_URL}/status", timeout=_STATUS_TIMEOUT_SECONDS,
                                headers=localconfig.auth_headers())
    except requests.RequestException as e:
        return {
            "connected": False,
            "bridge_reachable": False,
            "error_code": "BRIDGE_UNREACHABLE",
            "message": f"Could not reach the WhatsApp bridge: {e}",
            "last_message_at": _last_message_at(),
            "last_log_at": _last_log_at(),
        }
    if _refused(response):
        return {
            "connected": False,
            "bridge_reachable": True,
            "error_code": localconfig.BRIDGE_UNAUTHORIZED,
            "message": localconfig.UNAUTHORIZED_HINT,
        }
    try:
        status = response.json()
        if response.status_code != 200 or not isinstance(status, dict):
            raise ValueError
    except ValueError:
        return {
            "connected": False,
            "bridge_reachable": True,
            "error_code": "UNKNOWN",
            "message": f"The bridge answered HTTP {response.status_code} with an unexpected body",
        }
    return {**status, "bridge_reachable": True}


_BATCH_TYPES = ("image", "video", "audio", "document")  # what the bridge can download
_BATCH_MAX_ITEMS = 200


def _batch_error(message: str) -> dict:
    return {"success": False, "error_code": "INVALID_ARGUMENT", "message": message}


def _batch_item(message: Message, status: str, path: Optional[str] = None, error_code: Optional[str] = None,
                detail: Optional[str] = None, hint: Optional[str] = None) -> dict:
    item = {
        "message_id": message.id,
        "timestamp": _aware(message.timestamp).isoformat(),
        "media_type": message.media_type,
        "filename": message.filename,
        "status": status,
        "path": path,
    }
    if error_code:
        item["error_code"] = error_code
        item["message"] = detail
    if hint:
        item["hint"] = hint
    return item


def download_media_batch(
    chat_jid: str,
    after: Optional[str] = None,
    before: Optional[str] = None,
    types: Optional[List[str]] = None,
    max_size_mb: float = 25,
    max_items: int = 50,
    timeout_seconds: float = 120,
) -> dict:
    """Download the media of a chat inside a window, one at a time, and report each item."""
    if not chat_jid:
        return _batch_error("chat_jid is required")
    wanted = list(types) if types else list(_BATCH_TYPES)
    unknown = [t for t in wanted if t not in _BATCH_TYPES]
    if unknown:
        return _batch_error(f"Unknown types: {', '.join(map(str, unknown))}. Use: {', '.join(_BATCH_TYPES)}.")
    if not 1 <= max_items <= _BATCH_MAX_ITEMS:
        return _batch_error(f"max_items must be between 1 and {_BATCH_MAX_ITEMS}")
    if max_size_mb <= 0:
        return _batch_error("max_size_mb must be greater than 0")
    if not 1 <= timeout_seconds <= 600:
        return _batch_error("timeout_seconds must be between 1 and 600")
    try:
        conditions, params = ["messages.chat_jid = ?"], [chat_jid]
        if after:
            conditions.append(f"{_INSTANT} > ?")
            params.append(_instant_param(after, "after"))
        if before:
            conditions.append(f"{_INSTANT} < ?")
            params.append(_instant_param(before, "before"))
    except ValueError as e:
        return _batch_error(str(e))

    try:
        conn = _open_chats()
    except (sqlite3.Error, send_policy.DirectoryError) as e:
        return {"success": False, "error_code": "DIRECTORY_UNAVAILABLE", "message": f"Could not read the message database: {e}"}
    try:
        columns = _message_select(conn.cursor())
        conditions.append(f"messages.media_type IN ({','.join('?' * len(wanted))})")
        found = conn.execute(
            f"SELECT {columns} FROM messages JOIN chats ON messages.chat_jid = chats.jid "
            f"WHERE {' AND '.join(conditions)} ORDER BY {_INSTANT}, messages.rowid LIMIT ?",
            (*params, *wanted, max_items + 1)).fetchall()
    finally:
        conn.close()
    messages = [_message_from_row(row) for row in found]
    truncated = len(messages) > max_items
    messages = messages[:max_items]

    limit_bytes = max_size_mb * 1024 * 1024
    items, aborted = [], None
    for message in messages:
        if aborted:
            items.append(_batch_item(message, "skipped", error_code="NOT_ATTEMPTED",
                                     detail=f"Not attempted: the batch stopped ({aborted})"))
        elif message.local_path and os.path.isfile(message.local_path):
            items.append(_batch_item(message, "already_downloaded", path=message.local_path))
        elif message.file_length and message.file_length > limit_bytes:
            items.append(_batch_item(
                message, "skipped", error_code="SKIPPED_TOO_LARGE",
                detail=f"{format_size(message.file_length)} is over the limit of {max_size_mb:g} MB"))
        else:
            result = download_media(message.id, message.chat_jid, timeout=timeout_seconds)
            if result.success:
                items.append(_batch_item(message, "downloaded", path=result.path))
            else:
                items.append(_batch_item(message, "failed", error_code=result.error_code, detail=result.message,
                                         hint=result.hint))
                if result.error_code in ("BRIDGE_UNREACHABLE", localconfig.BRIDGE_UNAUTHORIZED):
                    aborted = result.error_code    # every other item would fail the same way

    count = lambda status: sum(1 for item in items if item["status"] == status)
    return {
        "success": True,
        "chat_jid": chat_jid,
        "total": len(items),
        "downloaded": count("downloaded"),
        "already_downloaded": count("already_downloaded"),
        "failed": count("failed"),
        "skipped": count("skipped"),
        "truncated": truncated,
        "aborted": aborted,
        "items": items,
    }


_THREAD_MAX_MESSAGES = 500
_THREAD_FETCH_LIMIT = 2000   # most messages read while walking down a chain, before the oldest 500 are kept
_THREAD_MAX_DEPTH = 50
_IN_CHUNK = 400


def _thread_error(code: str, message: str) -> dict:
    return {"success": False, "error_code": code, "message": message}


def _has_quoted_column(cursor: sqlite3.Cursor) -> bool:
    return any(row[1] == "quoted_id" for row in cursor.execute("PRAGMA table_info(messages)"))


def _fetch_by_ids(cursor: sqlite3.Cursor, columns: str, chat_jid: str, ids) -> List[Message]:
    ids, found = list(ids), []
    for start in range(0, len(ids), _IN_CHUNK):
        chunk = ids[start:start + _IN_CHUNK]
        cursor.execute(
            f"SELECT {columns} FROM messages JOIN chats ON messages.chat_jid = chats.jid "
            f"WHERE messages.chat_jid = ? AND messages.id IN ({','.join('?' * len(chunk))})", (chat_jid, *chunk))
        found.extend(_message_from_row(row) for row in cursor.fetchall())
    return found


def _fetch_children(cursor: sqlite3.Cursor, columns: str, chat_jid: str, parent_ids) -> List[Message]:
    """The messages of the chat that quote one of parent_ids, oldest first."""
    parent_ids, found = list(parent_ids), []
    for start in range(0, len(parent_ids), _IN_CHUNK):
        chunk = parent_ids[start:start + _IN_CHUNK]
        cursor.execute(
            f"SELECT {columns} FROM messages JOIN chats ON messages.chat_jid = chats.jid "
            f"WHERE messages.chat_jid = ? AND messages.quoted_id IN ({','.join('?' * len(chunk))}) "
            f"ORDER BY {_INSTANT}, messages.rowid", (chat_jid, *chunk))
        found.extend(_message_from_row(row) for row in cursor.fetchall())
    return found


def _resolve_ancestors(cursor, columns, chat_jid, cache: dict, missing: set, start: List[Message]) -> None:
    """Read every message the given ones quote, and those quote, up to the depth limit, into cache.

    What the database does not have goes to missing.
    """
    frontier = start
    for _ in range(_THREAD_MAX_DEPTH):
        wanted = {m.quoted_message_id for m in frontier if m.quoted_message_id} - cache.keys() - missing
        if not wanted:
            return
        frontier = _fetch_by_ids(cursor, columns, chat_jid, wanted)
        for message in frontier:
            cache[message.id] = message
        missing |= wanted - {m.id for m in frontier}


def _root_id(message: Message, cache: dict) -> str:
    """The top of the chain the message is in, as far as cache holds it.

    A chain that quotes itself in a circle has the oldest message of the circle as its root, for every member.
    """
    path, current = [], message
    while True:
        path.append(current)
        parent = cache.get(current.quoted_message_id) if current.quoted_message_id else None
        if parent is None:
            return current.id
        if any(parent.id == seen.id for seen in path):
            circle = path[[seen.id for seen in path].index(parent.id):]
            return min(circle, key=lambda m: (_aware(m.timestamp), m.rowid or 0)).id
        current = parent


def _media_brief(message: Message) -> Optional[dict]:
    if not message.media_type:
        return None
    return {"type": message.media_type, "filename": message.filename, "mimetype": message.mimetype,
            "size": message.file_length}


def _reply_ref(message: Message, cache: dict) -> Optional[dict]:
    if not message.quoted_message_id:
        return None
    quoted = cache.get(message.quoted_message_id)
    if quoted is None:
        return {"id": message.quoted_message_id, "found": False}
    return {
        "id": quoted.id,
        "found": True,
        "sender": quoted.sender,
        "sender_name": "Me" if quoted.is_from_me else sender_label(quoted.sender),
        "content": resolve_mentions(quoted.content),
        "timestamp": _aware(quoted.timestamp).isoformat(),
        "is_from_me": bool(quoted.is_from_me),
        "media": _media_brief(quoted),
    }


def _thread_message(message: Message, is_target: bool, cache: dict, replies: dict, roots: dict) -> dict:
    out = _message_dict(message, False)
    del out["is_match"]
    out.update({
        "forwarded": bool(message.forwarded),
        "is_target": is_target,
        "reply_to": _reply_ref(message, cache),
        "replies": replies.get(message.id, []),
        "thread_root_id": roots[message.id],
    })
    return out


def _with_quoted(cursor, columns, chat_jid, cache: dict, messages: List[Message]) -> dict:
    """cache plus the messages the given ones quote (one level), for showing reply_to."""
    wanted = {m.quoted_message_id for m in messages if m.quoted_message_id} - cache.keys()
    extra = {m.id: m for m in _fetch_by_ids(cursor, columns, chat_jid, wanted)} if wanted else {}
    return {**cache, **extra}


def thread_dicts(cursor: sqlite3.Cursor, columns: str, messages: List[Message]) -> List[dict]:
    """The get_message_thread form of messages, which may come from several chats."""
    out = {}
    for chat_jid in dict.fromkeys(m.chat_jid for m in messages):
        group = [m for m in messages if m.chat_jid == chat_jid]
        cache = {m.id: m for m in group}
        supported = _has_quoted_column(cursor)
        if supported:
            _resolve_ancestors(cursor, columns, chat_jid, cache, set(), group)
        roots = {m.id: _root_id(m, cache) for m in group}
        replies: dict = {}
        if supported:
            for child in _fetch_children(cursor, columns, chat_jid, [m.id for m in group]):
                replies.setdefault(child.quoted_message_id, []).append(child.id)
        for m in group:
            out[(chat_jid, m.id)] = _thread_message(m, False, cache, replies, roots)
    return [out[(m.chat_jid, m.id)] for m in messages]


def _thread_of(cursor, columns, target: Message, supported: bool) -> dict:
    cache, missing = {target.id: target}, set()
    if supported:
        _resolve_ancestors(cursor, columns, target.chat_jid, cache, missing, [target])
    root = cache[_root_id(target, cache)]

    members, truncated = {root.id: root}, False
    frontier = [root.id]
    while supported and frontier and not truncated:
        added = []
        for child in _fetch_children(cursor, columns, target.chat_jid, frontier):
            if child.id in members:
                continue
            if len(members) >= _THREAD_FETCH_LIMIT:
                truncated = True
                break
            members[child.id] = child
            added.append(child.id)
        frontier = added
    members[target.id] = target
    ordered = sorted(members.values(), key=lambda m: (_aware(m.timestamp), m.rowid))
    if len(ordered) > _THREAD_MAX_MESSAGES:
        truncated = True
        ordered = ordered[:_THREAD_MAX_MESSAGES]
        if target.id not in {m.id for m in ordered}:
            ordered = sorted(ordered[:-1] + [target], key=lambda m: (_aware(m.timestamp), m.rowid))
    cache.update(members)
    return {"root": root, "messages": ordered, "truncated": truncated, "cache": cache}


def get_message_thread(
    message_id: Optional[str] = None,
    chat_jid: Optional[str] = None,
    after: Optional[str] = None,
    before: Optional[str] = None,
    limit: Optional[int] = None,
    page: Optional[int] = None,
    order: Optional[str] = None,
) -> dict:
    """Messages with their replies linked: the chain around one message, or a window of a chat."""
    if message_id:
        if any(value is not None for value in (after, before, limit, page, order)):
            return _thread_error("INVALID_ARGUMENT", "With message_id, after, before, limit, page and order do not apply")
    else:
        if not chat_jid:
            return _thread_error("INVALID_ARGUMENT", "Give message_id, or chat_jid to read a window of a chat")
        limit, page, order = 20 if limit is None else limit, 0 if page is None else page, order or "asc"
        if limit < 1 or page < 0:
            return _thread_error("INVALID_ARGUMENT", "limit must be at least 1 and page at least 0")
        if order not in _ORDERS:
            return _thread_error("INVALID_ARGUMENT", f"Invalid order: {order}. Use one of: {', '.join(_ORDERS)}.")
        try:
            after_param = _instant_param(after, "after") if after else None
            before_param = _instant_param(before, "before") if before else None
        except ValueError as e:
            return _thread_error("INVALID_ARGUMENT", str(e))

    try:
        conn = _open_chats()
    except (sqlite3.Error, send_policy.DirectoryError) as e:
        return _thread_error("DIRECTORY_UNAVAILABLE", f"Could not read the message database: {e}")
    try:
        cursor = conn.cursor()
        columns = _message_select(cursor)
        supported = _has_quoted_column(cursor)

        if message_id:
            scope, params = ("AND messages.chat_jid = ?", (chat_jid,)) if chat_jid else ("", ())
            cursor.execute(f"SELECT {columns} FROM messages JOIN chats ON messages.chat_jid = chats.jid "
                           f"WHERE messages.id = ? {scope}", (message_id, *params))
            targets = [_message_from_row(row) for row in cursor.fetchall()]
            if not targets:
                return _thread_error("MESSAGE_NOT_FOUND", f"Message {message_id} was not found" +
                                     (f" in {chat_jid}" if chat_jid else ""))
            if len(targets) > 1:
                return _thread_error("AMBIGUOUS_MESSAGE", f"Message {message_id} exists in {len(targets)} chats; give chat_jid")
            target = targets[0]
            found = _thread_of(cursor, columns, target, supported)
            messages, cache = found["messages"], found["cache"]
            # roots first: the lookup below reads one more level only to show what each message quotes
            roots = {m.id: _root_id(m, cache) for m in messages}
            cache = _with_quoted(cursor, columns, target.chat_jid, cache, messages)
            result = {"success": True, "chat_jid": target.chat_jid, "root_id": found["root"].id,
                      "target_id": target.id, "total": len(messages), "truncated": found["truncated"]}
            targets_id = target.id
        else:
            conditions, params = ["messages.chat_jid = ?"], [chat_jid]
            if after_param:
                conditions.append(f"{_INSTANT} > ?")
                params.append(after_param)
            if before_param:
                conditions.append(f"{_INSTANT} < ?")
                params.append(before_param)
            source = "FROM messages JOIN chats ON messages.chat_jid = chats.jid WHERE " + " AND ".join(conditions)
            total = cursor.execute(f"SELECT COUNT(*) {source}", params).fetchone()[0]
            direction = order.upper()
            cursor.execute(f"SELECT {columns} {source} ORDER BY {_INSTANT} {direction}, messages.rowid {direction} "
                           f"LIMIT ? OFFSET ?", (*params, limit, page * limit))
            messages = [_message_from_row(row) for row in cursor.fetchall()]
            cache = {m.id: m for m in messages}
            if supported:
                _resolve_ancestors(cursor, columns, chat_jid, cache, set(), messages)
            roots = {m.id: _root_id(m, cache) for m in messages}
            has_more = page * limit + len(messages) < total
            result = {"success": True, "chat_jid": chat_jid, "total": total, "page": page, "limit": limit,
                      "has_more": has_more, "next_page": page + 1 if has_more else None}
            targets_id = None

        replies: dict = {}
        if supported and messages:
            for child in _fetch_children(cursor, columns, messages[0].chat_jid, [m.id for m in messages]):
                replies.setdefault(child.quoted_message_id, []).append(child.id)
        result["messages"] = [_thread_message(m, m.id == targets_id, cache, replies, roots) for m in messages]
        return result
    except sqlite3.Error as e:
        return _thread_error("UNKNOWN", f"Database error: {e}")
    finally:
        conn.close()
