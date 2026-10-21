from typing import List, Dict, Any, Optional
from mcp.server.fastmcp import FastMCP
from whatsapp import (
    search_contacts as whatsapp_search_contacts,
    list_messages as whatsapp_list_messages,
    list_chats as whatsapp_list_chats,
    get_chat as whatsapp_get_chat,
    get_direct_chat_by_contact as whatsapp_get_direct_chat_by_contact,
    get_contact_chats as whatsapp_get_contact_chats,
    get_last_interaction as whatsapp_get_last_interaction,
    get_message_context as whatsapp_get_message_context,
    send_message as whatsapp_send_message,
    send_file as whatsapp_send_file,
    send_audio_message as whatsapp_audio_voice_message,
    download_media as whatsapp_download_media,
    get_group_participants as whatsapp_get_group_participants,
    get_connection_status as whatsapp_get_connection_status
)

# Initialize FastMCP server
mcp = FastMCP("whatsapp")


def send_response(result) -> Dict[str, Any]:
    """Build a send tool's answer; message_id, timestamp and chat_jid appear only when the bridge reported them."""
    response: Dict[str, Any] = {"success": result.success, "message": result.message}
    if result.error_code:
        response["error_code"] = result.error_code
    if result.preview is not None:
        response["dry_run"] = True
        response["preview"] = result.preview
    if result.success:
        for field in ("message_id", "timestamp", "chat_jid"):
            value = getattr(result, field)
            if value:
                response[field] = value
    return response

@mcp.tool()
def search_contacts(query: str) -> List[Dict[str, Any]]:
    """Search WhatsApp contacts by name or phone number.
    
    Args:
        query: Search term to match against contact names or phone numbers
    """
    contacts = whatsapp_search_contacts(query)
    return contacts

@mcp.tool()
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
) -> Any:
    """Get WhatsApp messages matching specified criteria with optional context.
    
    Args:
        after: Optional ISO-8601 date or datetime, with a UTC offset (2026-10-21T22:12:00-03:00), to only return messages after this instant.
               Without an offset it is read in the WHATSAPP_TIMEZONE zone (an IANA name such as America/Recife) or, when that is not set, in the system zone
        before: Optional ISO-8601 date or datetime to only return messages before this instant; same zone rule as after
        sender_phone_number: Optional phone number to filter messages by sender
        chat_jid: Optional chat JID to filter messages by chat
        chat_name: Optional part of the chat name (any case) to filter messages by chat; combines with chat_jid
        query: Optional search term to filter messages by content
        limit: Maximum number of messages to return (default 20)
        page: Page number for pagination (default 0)
        order: "desc" (newest first, default) or "asc" (oldest first); pages follow this order
        format: "text" (default) or "structured"
        include_context: Whether to include messages before and after matches (default True).
                         The context is grouped by chat, chronological, without repeats; the found messages are marked
                         (">>" in text, is_match=true in structured)
        context_before: Number of messages to include before each match (default 1)
        context_after: Number of messages to include after each match (default 1)

    Returns:
        With format="text", the messages as text. With format="structured", a dict: messages (each with id, timestamp in ISO-8601
        with offset, chat_jid, chat_name, sender, sender_name, content, is_from_me, is_match and media), total (matches, without
        context), page, limit, has_more and next_page (null on the last page)
    """
    messages = whatsapp_list_messages(
        after=after,
        before=before,
        sender_phone_number=sender_phone_number,
        chat_jid=chat_jid,
        query=query,
        limit=limit,
        page=page,
        include_context=include_context,
        context_before=context_before,
        context_after=context_after,
        format=format,
        order=order,
        chat_name=chat_name
    )
    return messages

@mcp.tool()
def list_chats(
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_last_message: bool = True,
    sort_by: str = "last_active"
) -> List[Dict[str, Any]]:
    """Get WhatsApp chats matching specified criteria.
    
    Args:
        query: Optional search term to filter chats by name or JID
        limit: Maximum number of chats to return (default 20)
        page: Page number for pagination (default 0)
        include_last_message: Whether to include the last message in each chat (default True)
        sort_by: Field to sort results by, either "last_active" or "name" (default "last_active")
    """
    chats = whatsapp_list_chats(
        query=query,
        limit=limit,
        page=page,
        include_last_message=include_last_message,
        sort_by=sort_by
    )
    return chats

@mcp.tool()
def get_chat(chat_jid: str, include_last_message: bool = True) -> Dict[str, Any]:
    """Get WhatsApp chat metadata by JID.
    
    Args:
        chat_jid: The JID of the chat to retrieve
        include_last_message: Whether to include the last message (default True)
    """
    chat = whatsapp_get_chat(chat_jid, include_last_message)
    return chat

@mcp.tool()
def get_direct_chat_by_contact(sender_phone_number: str) -> Dict[str, Any]:
    """Get WhatsApp chat metadata by sender phone number.
    
    Args:
        sender_phone_number: The phone number to search for
    """
    chat = whatsapp_get_direct_chat_by_contact(sender_phone_number)
    return chat

@mcp.tool()
def get_contact_chats(jid: str, limit: int = 20, page: int = 0) -> List[Dict[str, Any]]:
    """Get all WhatsApp chats involving the contact.
    
    Args:
        jid: The contact's JID to search for
        limit: Maximum number of chats to return (default 20)
        page: Page number for pagination (default 0)
    """
    chats = whatsapp_get_contact_chats(jid, limit, page)
    return chats

@mcp.tool()
def get_last_interaction(jid: str) -> str:
    """Get most recent WhatsApp message involving the contact.
    
    Args:
        jid: The JID of the contact to search for
    """
    message = whatsapp_get_last_interaction(jid)
    return message

@mcp.tool()
def get_message_context(
    message_id: str,
    before: int = 5,
    after: int = 5,
    chat_jid: Optional[str] = None
) -> Dict[str, Any]:
    """Get context around a specific WhatsApp message, both sides in chronological order.
    
    Args:
        message_id: The ID of the message to get context for
        before: Number of messages to include before the target message (default 5)
        after: Number of messages to include after the target message (default 5)
        chat_jid: Optional chat JID, for when the same message ID exists in more than one chat
    """
    context = whatsapp_get_message_context(message_id, before, after, chat_jid)
    return context

@mcp.tool()
def send_message(
    recipient: str,
    message: str,
    override_token: Optional[str] = None,
    mentions: Optional[List[str]] = None,
    reply_to: Optional[str] = None,
    dry_run: bool = False
) -> Dict[str, Any]:
    """Send a WhatsApp message to a person or group. For group chats use the JID.

    Args:
        recipient: The recipient - either a phone number with country code but no + or other symbols,
                 or a JID (e.g., "123456789@s.whatsapp.net" or a group JID like "123456789@g.us")
        message: The message text to send. Formatting (*bold*, _italic_, > quote) is sent as written.
                 Write @+5551981993271 (or @<number>, @<lid>) to really tag a person: the token becomes @<digits>
                 and the person is notified. Text without spaces inside the token; 7 to 20 digits
        override_token: Optional token (set by the user in send_policy.json) to release one send the send policy blocks
        mentions: Optional people to tag besides the @tokens in the text: JIDs, phone numbers or LIDs
        reply_to: Optional ID of a message of that chat to quote (REPLY_TARGET_NOT_FOUND when it is not there)
        dry_run: When true nothing is sent: returns preview with the final text, the resolved chat (name and JID),
                 the mentions and the quoted message, so it can be reviewed first. The send policy still applies
    
    Returns:
        A dictionary containing success status and a status message and, when sent, the message_id, timestamp and chat_jid.
        A dry run returns dry_run=true and preview.
        A send the policy blocks returns success=false with error_code=SEND_BLOCKED_BY_POLICY and nothing is sent;
        a recipient name that matches no chat or several returns RECIPIENT_NOT_FOUND or RECIPIENT_AMBIGUOUS
    """
    # Validate input
    if not recipient:
        return {
            "success": False,
            "message": "Recipient must be provided"
        }
    
    # Call the whatsapp_send_message function with the unified recipient parameter
    return send_response(whatsapp_send_message(recipient, message, override_token, mentions, reply_to, dry_run))

@mcp.tool()
def send_file(recipient: str, media_path: str, override_token: Optional[str] = None) -> Dict[str, Any]:
    """Send a file such as a picture, raw audio, video or document via WhatsApp to the specified recipient. For group messages use the JID.
    
    Args:
        recipient: The recipient - either a phone number with country code but no + or other symbols,
                 or a JID (e.g., "123456789@s.whatsapp.net" or a group JID like "123456789@g.us")
        media_path: The absolute path to the media file to send (image, video, document)
        override_token: Optional token (set by the user in send_policy.json) to release one send the send policy blocks
    
    Returns:
        A dictionary containing success status and a status message and, when sent, the message_id, timestamp and chat_jid
    """
    
    # Call the whatsapp_send_file function
    return send_response(whatsapp_send_file(recipient, media_path, override_token))

@mcp.tool()
def send_audio_message(recipient: str, media_path: str, override_token: Optional[str] = None) -> Dict[str, Any]:
    """Send any audio file as a WhatsApp audio message to the specified recipient. For group messages use the JID. If it errors due to ffmpeg not being installed, use send_file instead.
    
    Args:
        recipient: The recipient - either a phone number with country code but no + or other symbols,
                 or a JID (e.g., "123456789@s.whatsapp.net" or a group JID like "123456789@g.us")
        media_path: The absolute path to the audio file to send (will be converted to Opus .ogg if it's not a .ogg file)
        override_token: Optional token (set by the user in send_policy.json) to release one send the send policy blocks
    
    Returns:
        A dictionary containing success status and a status message and, when sent, the message_id, timestamp and chat_jid
    """
    return send_response(whatsapp_audio_voice_message(recipient, media_path, override_token))

@mcp.tool()
def download_media(message_id: str, chat_jid: str) -> Dict[str, Any]:
    """Download media from a WhatsApp message and get the local file path.
    
    Args:
        message_id: The ID of the message containing the media
        chat_jid: The JID of the chat containing the message
    
    Returns:
        On success: success, message and file_path. On failure: success=false, the bridge's real message, an error_code
        (LINK_EXPIRED_PHONE_OFFLINE, MEDIA_NOT_FOUND, BRIDGE_UNREACHABLE or UNKNOWN) and, when actionable, a hint
    """
    result = whatsapp_download_media(message_id, chat_jid)

    if result.success:
        return {
            "success": True,
            "message": result.message,
            "file_path": result.path
        }
    response = {
        "success": False,
        "message": result.message,
        "error_code": result.error_code
    }
    if result.hint:
        response["hint"] = result.hint
    return response

@mcp.tool()
def get_group_participants(chat_jid: str) -> Dict[str, Any]:
    """List the participants of a WhatsApp group with their name, phone number, LID and admin status.

    Args:
        chat_jid: The JID of the group (e.g., "123456789@g.us")

    Returns:
        On success: success=true and participants, a list of {name, phone, lid, is_admin}.
        A participant WhatsApp has no name for has an empty name; phone or lid can be empty when unknown.
        On failure: success=false and a message.
    """
    result = whatsapp_get_group_participants(chat_jid)
    if result.success:
        return {"success": True, "participants": result.participants}
    return {"success": False, "message": result.message}

@mcp.tool()
def get_connection_status() -> Dict[str, Any]:
    """Check whether the WhatsApp bridge is running and connected.

    Returns:
        connected (is the bridge connected to WhatsApp), bridge_reachable, logged_in, phone, version, started_at,
        uptime_seconds, last_event_at, last_message_at, last_connected_at, last_disconnected_at (null when it
        has not happened since the bridge started) and pending_media_retries (media waiting for the phone to re-upload).
        When the bridge does not answer: connected=false, bridge_reachable=false, error_code=BRIDGE_UNREACHABLE,
        last_message_at (newest stored message) and last_log_at (last write to the bridge log)
    """
    return whatsapp_get_connection_status()

if __name__ == "__main__":
    # Initialize and run the server
    mcp.run(transport='stdio')