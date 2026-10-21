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
import send_policy
import logging
import re

MESSAGES_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'whatsapp-bridge', 'store', 'messages.db')
logger = logging.getLogger(__name__)

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
        conn = sqlite3.connect(MESSAGES_DB_PATH)
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
        conn = sqlite3.connect(MESSAGES_DB_PATH)
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
        return f"@{name}" if name else match.group(0)
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
        conn = sqlite3.connect(MESSAGES_DB_PATH)
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
        conn = sqlite3.connect(MESSAGES_DB_PATH)
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
        conn = sqlite3.connect(MESSAGES_DB_PATH)
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
        conn = sqlite3.connect(MESSAGES_DB_PATH)
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
        conn = sqlite3.connect(MESSAGES_DB_PATH)
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
        conn = sqlite3.connect(MESSAGES_DB_PATH)
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
        conn = sqlite3.connect(MESSAGES_DB_PATH)
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
        conn = sqlite3.connect(MESSAGES_DB_PATH)
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


def _post_send(payload: dict) -> SendResult:
    """POST a send request to the bridge and parse its answer."""
    try:
        response = requests.post(f"{WHATSAPP_API_BASE_URL}/send", json=payload)
    except requests.RequestException as e:
        return SendResult(False, f"Request error: {str(e)}")
    except Exception as e:
        return SendResult(False, f"Unexpected error: {str(e)}")

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


def get_group_participants(chat_jid: str) -> GroupParticipantsResult:
    """List a group's participants (name, phone, LID, is_admin) through the bridge."""
    if not chat_jid:
        return GroupParticipantsResult(False, "chat_jid must be provided")
    try:
        response = requests.post(f"{WHATSAPP_API_BASE_URL}/group_participants", json={"chat_jid": chat_jid})
    except requests.RequestException as e:
        return GroupParticipantsResult(False, f"Could not reach the WhatsApp bridge: {e}")
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
    hint = LINK_EXPIRED_HINT if code == "LINK_EXPIRED_PHONE_OFFLINE" else None
    logger.error("download_media failed (%s): %s", code, message)
    return DownloadResult(False, message, error_code=code, hint=hint)


def download_media(message_id: str, chat_jid: str) -> DownloadResult:
    """Download media from a message through the bridge.

    Args:
        message_id: The ID of the message containing the media
        chat_jid: The JID of the chat containing the message

    Returns:
        A DownloadResult with the local path on success, or the bridge's real
        error message and a stable error_code on failure.
    """
    try:
        response = requests.post(
            f"{WHATSAPP_API_BASE_URL}/download",
            json={"message_id": message_id, "chat_jid": chat_jid},
        )
    except requests.RequestException as e:
        return _download_failure(f"Could not reach the WhatsApp bridge: {e}", "BRIDGE_UNREACHABLE")

    try:
        result = response.json()
    except ValueError:
        return _download_failure(f"HTTP {response.status_code} - {response.text}")

    if response.status_code == 200 and result.get("success", False):
        return DownloadResult(True, "Media downloaded successfully", path=result.get("path"))
    return _download_failure(result.get("message") or f"HTTP {response.status_code}")
