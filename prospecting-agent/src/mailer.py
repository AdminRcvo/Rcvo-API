from __future__ import annotations
import hashlib,html,os,smtplib,ssl
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any

def content_hash(subject:str,text:str,html_body:str|None)->str:
    raw=(subject+"\n"+text+"\n"+(html_body or "")).encode()
    return hashlib.sha256(raw).hexdigest()

def safe_format(template:str,data:dict[str,Any])->str:
    class D(dict):
        def __missing__(self,key): return ""
    return template.format_map(D({k:"" if v is None else str(v) for k,v in data.items()}))

def build_message(
    *,
    mailbox:dict[str,Any],
    to_email:str,
    subject:str,
    text_body:str,
    html_body:str|None,
    unsubscribe_url:str,
    message_id:str,
    identity_name:str,
    identity_address:str,
)->EmailMessage:
    address_line=(f"\n{identity_address}" if identity_address else "")
    visible_footer=(
        f"\n\n—\n{identity_name}{address_line}\n"
        f"Pour ne plus recevoir de messages de Rcvo : {unsubscribe_url}"
    )
    text_final=text_body.rstrip()+visible_footer
    msg=EmailMessage()
    msg["From"]=formataddr((mailbox.get("from_name") or identity_name,mailbox["address"]))
    msg["To"]=to_email
    msg["Subject"]=subject
    msg["Message-ID"]=message_id
    msg["Reply-To"]=mailbox.get("reply_to") or mailbox["address"]
    msg["List-Unsubscribe"]=f"<{unsubscribe_url}>"
    msg["List-Unsubscribe-Post"]="List-Unsubscribe=One-Click"
    msg.set_content(text_final)

    if html_body:
        footer=(
            '<hr><p style="font-size:12px">'
            +html.escape(identity_name)
            +(("<br>"+html.escape(identity_address)) if identity_address else "")
            +' — <a href="'+html.escape(unsubscribe_url,quote=True)+'">Se désinscrire</a></p>'
        )
        msg.add_alternative(html_body.rstrip()+footer,subtype="html")
    return msg

class DryRunTransport:
    def send(self,message,mailbox):
        return {"accepted":True,"provider_message_id":message["Message-ID"],"dry_run":True}

class SmtpTransport:
    def send(self,message,mailbox):
        host=str(mailbox["smtp_host"])
        port=int(mailbox.get("smtp_port",587))
        mode=str(mailbox.get("smtp_security") or "starttls").lower()
        username=os.getenv(str(mailbox.get("username_env") or ""))
        password=os.getenv(str(mailbox.get("password_env") or ""))
        if not username or not password:
            raise RuntimeError("SMTP credentials missing")
        timeout=float(mailbox.get("smtp_timeout",30))
        context=ssl.create_default_context()
        if mode=="ssl":
            server=smtplib.SMTP_SSL(host,port,timeout=timeout,context=context)
        else:
            server=smtplib.SMTP(host,port,timeout=timeout)
        try:
            server.ehlo()
            if mode=="starttls":
                server.starttls(context=context)
                server.ehlo()
            elif mode!="ssl":
                raise RuntimeError("unencrypted SMTP is forbidden")
            server.login(username,password)
            refused=server.send_message(message)
            if refused:
                raise RuntimeError("recipient refused: "+str(refused))
            return {"accepted":True,"provider_message_id":message["Message-ID"]}
        finally:
            try: server.quit()
            except Exception: server.close()

def transport_for(mailbox:dict[str,Any]):
    kind=str(mailbox.get("transport") or "smtp").lower()
    if kind=="dry_run": return DryRunTransport()
    if kind=="smtp": return SmtpTransport()
    raise ValueError("unsupported mail transport: "+kind)
