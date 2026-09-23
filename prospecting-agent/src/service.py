from __future__ import annotations
import argparse,html,json,os,threading,time,traceback
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from agent import ProspectingAgent,RuntimeConfig
from reference_gateway import ReferenceHttpGateway
from state import State
from tokens import verify

ROOT=Path(__file__).resolve().parents[1]

def load_json(path:Path):
    return json.loads(path.read_text(encoding="utf-8"))

def build_agent(args):
    ref_url=args.reference_url or os.getenv("RCVO_REFERENCE_BASE_URL")
    token=os.getenv("RCVO_REFERENCE_PROSPECTION_TOKEN")
    secret=os.getenv("RCVO_PROSPECTING_TRACKING_SECRET")
    if not ref_url or not token or not secret:
        raise SystemExit("RCVO_REFERENCE_BASE_URL, RCVO_REFERENCE_PROSPECTION_TOKEN and RCVO_PROSPECTING_TRACKING_SECRET are required")
    campaign=load_json(args.campaign)
    mailboxes=load_json(args.mailboxes)["mailboxes"]
    identity=load_json(args.identity)
    public_base=args.public_base_url or os.getenv("RCVO_PROSPECTING_PUBLIC_BASE_URL")
    if not public_base:
        raise SystemExit("RCVO_PROSPECTING_PUBLIC_BASE_URL is required")
    return ProspectingAgent(
        ReferenceHttpGateway(ref_url,token,args.http_timeout),
        State(args.state_db),
        RuntimeConfig(
            campaign=campaign,mailboxes=mailboxes,identity=identity,
            public_base_url=public_base,tracking_secret=secret,
            claim_size=args.claim_size,lease_seconds=args.lease_seconds,
            enroll_limit=args.enroll_limit,worker_id=args.worker_id,
            enforce_send_window=not args.ignore_send_window,
        )
    )

class Server(ThreadingHTTPServer):
    agent:ProspectingAgent
    feedback_token:str

class Handler(BaseHTTPRequestHandler):
    def log_message(self,fmt,*args): return
    def _json(self,status,payload):
        body=json.dumps(payload,ensure_ascii=False,separators=(",",":"),default=str).encode()
        self.send_response(status); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def _body(self,max_bytes=1024*1024):
        length=int(self.headers.get("Content-Length","0"))
        if length>max_bytes: raise ValueError("body too large")
        raw=self.rfile.read(length) if length else b"{}"
        ctype=self.headers.get("Content-Type","")
        if "json" in ctype: return json.loads(raw.decode() or "{}")
        return {"raw":raw.decode(errors="replace")}

    def do_GET(self):
        path=urlparse(self.path).path
        if path in ("/health","/ready"):
            try:
                data={"status":"ok","reference":self.server.agent.reference.health(),"state":self.server.agent.state.stats()}
                self._json(200,data)
            except Exception as exc: self._json(503,{"status":"error","error":str(exc)})
            return
        if path.startswith("/unsubscribe/"):
            token=path.rsplit("/",1)[1]
            try:
                p=verify(token,self.server.agent.config.tracking_secret)
                if p.get("kind")!="unsubscribe": raise ValueError("wrong token")
                self.server.agent.request_unsubscribe(
                    p["contact_rcvo_id"],p.get("campaign_rcvo_id")
                )
                try:
                    self.server.agent.reference.events([{
                        "event_type":"unsubscribe_requested",
                        "event_key":"unsubscribe:"+token[-24:],
                        "contact_rcvo_id":p["contact_rcvo_id"],
                        "campaign_rcvo_id":p.get("campaign_rcvo_id"),
                        "result":"user_unsubscribed",
                        "source":"unsubscribe_link",
                    }])
                except Exception:
                    pass
                body=("<!doctype html><meta charset=utf-8><title>Désinscription Rcvo</title>"
                      "<h1>Désinscription confirmée</h1><p>Cette adresse ne recevra plus de sollicitations Rcvo.</p>").encode()
                self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8")
                self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
            except Exception as exc: self._json(400,{"error":str(exc)})
            return
        if path.startswith("/video/"):
            token=path.rsplit("/",1)[1]
            try:
                p=verify(token,self.server.agent.config.tracking_secret)
                if p.get("kind")!="video": raise ValueError("wrong token")
                target=str(p["target"])
                safe=html.escape(target,quote=True)
                if target.lower().split("?",1)[0].endswith((".mp4",".webm",".ogg")):
                    player=f'<video id="rcvoVideo" controls preload="metadata" style="max-width:100%" src="{safe}"></video>'
                    js=f"""<script>
                    let sent=false; document.getElementById('rcvoVideo').addEventListener('play',()=>{{
                      if(!sent){{sent=true;fetch('/video-event/{token}',{{method:'POST',keepalive:true}});}}
                    }});
                    </script>"""
                else:
                    player=f'<p><a id="rcvoVideoLink" href="{safe}" target="_blank" rel="noopener">Voir la vidéo Rcvo</a></p>'
                    js=f"""<script>document.getElementById('rcvoVideoLink').addEventListener('click',()=>fetch('/video-event/{token}',{{method:'POST',keepalive:true}}));</script>"""
                body=("<!doctype html><meta charset=utf-8><title>Rcvo</title><main>"
                      "<h1>Rcvo</h1>"+player+"</main>"+js).encode()
                self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8")
                self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
            except Exception as exc: self._json(400,{"error":str(exc)})
            return
        self._json(404,{"error":"not_found"})

    def do_POST(self):
        path=urlparse(self.path).path
        if path.startswith("/unsubscribe/"):
            return self.do_GET()
        if path.startswith("/video-event/"):
            token=path.rsplit("/",1)[1]
            try:
                p=verify(token,self.server.agent.config.tracking_secret)
                if p.get("kind")!="video": raise ValueError("wrong token")
                result=self.server.agent.reference.events([{
                    "event_type":"video_viewed",
                    "event_key":"video:"+str(time.time_ns()),
                    "contact_rcvo_id":p["contact_rcvo_id"],
                    "campaign_rcvo_id":p.get("campaign_rcvo_id"),
                    "message_key":p.get("message_key"),
                    "result":"play",
                    "source":"video_landing",
                }])
                self.send_response(204); self.end_headers()
            except Exception as exc: self._json(400,{"error":str(exc)})
            return
        if path=="/events/provider":
            if self.headers.get("Authorization")!="Bearer "+self.server.feedback_token:
                self._json(401,{"error":"unauthorized"}); return
            try: self._json(200,self.server.agent.handle_feedback(self._body()))
            except Exception as exc: self._json(400,{"error":str(exc)})
            return
        self._json(404,{"error":"not_found"})

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--state-db",type=Path,default=ROOT/"data"/"prospecting-agent.sqlite")
    p.add_argument("--campaign",type=Path,default=ROOT/"config"/"campaign.json")
    p.add_argument("--mailboxes",type=Path,default=ROOT/"config"/"mailboxes.json")
    p.add_argument("--identity",type=Path,default=ROOT/"config"/"identity.json")
    p.add_argument("--reference-url")
    p.add_argument("--public-base-url")
    p.add_argument("--claim-size",type=int,default=100)
    p.add_argument("--lease-seconds",type=int,default=300)
    p.add_argument("--enroll-limit",type=int,default=5000)
    p.add_argument("--http-timeout",type=float,default=30)
    p.add_argument("--worker-id")
    p.add_argument("--ignore-send-window",action="store_true")
    p.add_argument("--bind",default="0.0.0.0")
    p.add_argument("--port",type=int,default=int(os.getenv("PORT","8080")))
    args=p.parse_args()
    agent=build_agent(args)
    agent.bootstrap()
    feedback=os.getenv("RCVO_PROSPECTING_FEEDBACK_TOKEN")
    if not feedback: raise SystemExit("RCVO_PROSPECTING_FEEDBACK_TOKEN is required")
    server=Server((args.bind,args.port),Handler); server.agent=agent; server.feedback_token=feedback
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    while True:
        try:
            agent.process_once()
        except Exception:
            traceback.print_exc()
        time.sleep(5)

if __name__=="__main__": main()
