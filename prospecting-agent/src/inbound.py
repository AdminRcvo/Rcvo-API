from __future__ import annotations

import imaplib,os,ssl
from email import policy
from email.parser import BytesParser
from email.message import Message
from typing import Any

def _original_message_id(msg:Message)->str|None:
    for header in ("In-Reply-To","X-Original-Message-ID","Original-Message-ID"):
        value=msg.get(header)
        if value:
            return str(value).strip().split()[-1]
    refs=msg.get("References")
    if refs:
        values=str(refs).split()
        if values: return values[-1]
    for part in msg.walk():
        if part.get_content_type()=="message/rfc822":
            payload=part.get_payload()
            if isinstance(payload,list) and payload:
                mid=payload[0].get("Message-ID")
                if mid: return str(mid).strip()
    return None

def classify(raw:bytes)->dict[str,Any]|None:
    msg=BytesParser(policy=policy.default).parsebytes(raw)
    original=_original_message_id(msg)
    content_type=msg.get_content_type()
    report_type=(msg.get_param("report-type") or "").lower()

    if content_type=="multipart/report" or report_type=="delivery-status":
        action=None; status=None; diagnostic=None
        for part in msg.walk():
            if part.get_content_type()=="message/delivery-status":
                payload=part.get_payload()
                if isinstance(payload,list):
                    for block in payload:
                        action=action or block.get("Action")
                        status=status or block.get("Status")
                        diagnostic=diagnostic or block.get("Diagnostic-Code")
        a=(action or "").lower()
        st=(status or "").strip()
        if a=="failed" or st.startswith("5"):
            kind="bounce"
        elif a=="delayed" or st.startswith("4"):
            kind="soft_bounce"
        elif a=="delivered" or st.startswith("2"):
            kind="email_delivered"
        else:
            kind="delivery_rejected"
        if not original:
            return None
        return {
            "event_type":kind,"message_id":original,
            "result":diagnostic or action or status or kind,
        }

    if original:
        return {
            "event_type":"reply_received",
            "message_id":original,
            "result":"reply_received",
            "subject":str(msg.get("Subject") or ""),
            "from":str(msg.get("From") or ""),
        }
    return None

def scan_mailbox(mailbox:dict[str,Any],handler,limit:int=100)->dict[str,int]:
    if not mailbox.get("imap_enabled",False):
        return {"processed":0,"ignored":0}
    host=str(mailbox["imap_host"])
    port=int(mailbox.get("imap_port",993))
    security=str(mailbox.get("imap_security") or "ssl").lower()
    username=os.getenv(str(mailbox.get("imap_username_env") or mailbox.get("username_env") or ""))
    password=os.getenv(str(mailbox.get("imap_password_env") or mailbox.get("password_env") or ""))
    if not username or not password:
        raise RuntimeError("IMAP credentials missing")
    context=ssl.create_default_context()
    if security=="ssl":
        client=imaplib.IMAP4_SSL(host,port,ssl_context=context)
    elif security=="starttls":
        client=imaplib.IMAP4(host,port)
        client.starttls(ssl_context=context)
    else:
        raise RuntimeError("unencrypted IMAP is forbidden")
    processed=ignored=0
    try:
        client.login(username,password)
        typ,_=client.select(str(mailbox.get("imap_folder") or "INBOX"))
        if typ!="OK": raise RuntimeError("cannot select IMAP folder")
        typ,data=client.uid("search",None,"UNSEEN")
        if typ!="OK": raise RuntimeError("IMAP search failed")
        uids=(data[0].split() if data and data[0] else [])[:limit]
        for uid in uids:
            typ,parts=client.uid("fetch",uid,"(RFC822)")
            if typ!="OK": continue
            raw=None
            for item in parts:
                if isinstance(item,tuple) and isinstance(item[1],bytes):
                    raw=item[1]; break
            if not raw: continue
            event=classify(raw)
            if event:
                event["event_key"]=f"imap:{mailbox['mailbox_id']}:{uid.decode(errors='ignore')}"
                handler(event)
                processed+=1
                client.uid("store",uid,"+FLAGS","(\\Seen)")
            else:
                ignored+=1
        return {"processed":processed,"ignored":ignored}
    finally:
        try: client.logout()
        except Exception: pass
