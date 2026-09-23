from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from profiles import profile_value

FREE_EMAIL_DOMAINS={
    "gmail.com","googlemail.com","outlook.com","hotmail.com","hotmail.fr","live.fr",
    "live.com","orange.fr","wanadoo.fr","free.fr","yahoo.fr","yahoo.com","icloud.com",
    "laposte.net","sfr.fr"
}

ALIASES={
    "email":[
        "email","e-mail","mail","email_address","emailaddress","work_email",
        "professional_email","business_email","courriel"
    ],
    "first_name":["first_name","firstname","given_name","prenom","prénom"],
    "last_name":["last_name","lastname","surname","family_name","nom"],
    "full_name":["full_name","fullname","contact_name","person_name","nom_complet"],
    "phone":["phone","phone_number","telephone","téléphone","mobile","mobile_phone"],
    "job_title":["job_title","title","position","fonction","poste","role_title"],
    "job_role":["job_role","role","department_role","fonction_normalisee"],
    "company_name":[
        "company","company_name","organization","organization_name","organisation",
        "entreprise","societe","société","dealer","dealer_name","dealership"
    ],
    "company_domain":["company_domain","domain","website_domain","company_website","website","site_web"],
    "company_city":["company_city","organization_city","dealer_city","ville_entreprise","city_company"],
    "city":["city","ville","town","locality"],
    "postal_code":["postal_code","postcode","zip","zip_code","code_postal"],
    "country":["country","country_code","pays"],
    "external_contact_id":["contact_id","person_id","lead_id","external_contact_id"],
    "external_company_id":["company_id","organization_id","account_id","external_company_id"],
    "linkedin_url":["linkedin_url","linkedin","linkedin_profile"],
    "source_url":["source_url","profile_url","url","website_url"],
}

AUTO_TERMS={
    "automobile","automotive","auto","véhicule","vehicule","car","cars","dealer",
    "dealership","concession","garage","motor","motors","vehicle","vehicles"
}
VO_TERMS={
    "vo","véhicule occasion","vehicule occasion","véhicules occasion","vehicules occasion",
    "occasion","used car","used cars","used vehicle","used vehicles","pre-owned","preowned"
}
SALES_TERMS={
    "vendeur","vendeuse","commercial","sales","responsable","directeur","manager",
    "chef des ventes","conseiller commercial","salesperson","sales manager"
}

def _key(value:str) -> str:
    v=unicodedata.normalize("NFKD",value)
    v="".join(ch for ch in v if not unicodedata.combining(ch))
    v=v.casefold()
    return re.sub(r"[^a-z0-9]+","_",v).strip("_")

ALIAS_KEYS={target:{_key(x) for x in aliases} for target,aliases in ALIASES.items()}

def _clean(value:Any) -> str|None:
    if value is None:
        return None
    if isinstance(value,(dict,list,tuple,set)):
        return None
    v=re.sub(r"\s+"," ",str(value)).strip()
    return v or None

def flatten(data:Any,prefix:str="") -> dict[str,list[Any]]:
    out:dict[str,list[Any]]={}
    if isinstance(data,dict):
        for k,v in data.items():
            name=_key(str(k))
            path=f"{prefix}_{name}" if prefix else name
            out.setdefault(name,[]).append(v)
            out.setdefault(path,[]).append(v)
            nested=flatten(v,path)
            for nk,nv in nested.items():
                out.setdefault(nk,[]).extend(nv)
    elif isinstance(data,list):
        for item in data[:100]:
            nested=flatten(item,prefix)
            for nk,nv in nested.items():
                out.setdefault(nk,[]).extend(nv)
    return out

def first(flat:dict[str,list[Any]],target:str) -> str|None:
    aliases=ALIAS_KEYS[target]
    for key,values in flat.items():
        leaf=key.rsplit("_",1)[-1]
        if key in aliases or leaf in aliases:
            for value in values:
                v=_clean(value)
                if v:
                    return v
    return None

def normalize_email(value:Any) -> str|None:
    v=_clean(value)
    if not v or "@" not in v:
        return None
    local,domain=v.rsplit("@",1)
    local=local.strip()
    domain=domain.strip().lower().strip(".")
    if not local or "." not in domain:
        return None
    return f"{local.lower()}@{domain}"

def domain_from(value:Any) -> str|None:
    v=_clean(value)
    if not v:
        return None
    v=v.casefold()
    v=re.sub(r"^https?://","",v)
    v=v.split("/",1)[0].split(":",1)[0].strip(".")
    if v.startswith("www."):
        v=v[4:]
    if "@" in v:
        v=v.rsplit("@",1)[1]
    return v if "." in v else None

def normal_last(value:Any) -> str|None:
    v=_clean(value)
    return v.upper() if v else None

def normal_first(value:Any) -> str|None:
    v=_clean(value)
    if not v:
        return None
    return "-".join(p[:1].upper()+p[1:].lower() if p else "" for p in v.split("-"))

def split_full_name(value:Any) -> tuple[str|None,str|None]:
    v=_clean(value)
    if not v:
        return None,None
    parts=v.split()
    if len(parts)<2:
        return None,None
    return normal_first(parts[0]),normal_last(" ".join(parts[1:]))

def text_blob(payload:Any) -> str:
    parts=[]
    def walk(x:Any):
        if isinstance(x,dict):
            for k,v in x.items():
                parts.append(str(k))
                walk(v)
        elif isinstance(x,list):
            for v in x[:100]:
                walk(v)
        elif x is not None:
            parts.append(str(x))
    walk(payload)
    return unicodedata.normalize("NFKD"," ".join(parts)).casefold()

def has_any(blob:str,terms:set[str]) -> bool:
    for term in terms:
        normalized=unicodedata.normalize("NFKD",term).casefold()
        if " " in normalized or "-" in normalized:
            if normalized in blob:
                return True
        elif re.search(r"(?<![\w])"+re.escape(normalized)+r"(?![\w])",blob):
            return True
    return False

def normalize_role(title:str|None,role:str|None) -> str|None:
    text=(role or title or "").casefold()
    if not text:
        return None
    if "responsable vo" in text or "chef des ventes vo" in text:
        return "responsable_vo"
    if "directeur" in text and ("commercial" in text or "vente" in text):
        return "direction_commerciale"
    if "vendeur" in text or "vendeuse" in text or "conseiller commercial" in text:
        return "vendeur_vo" if (" vo" in " "+text or "occasion" in text) else "vente_auto"
    if "sales manager" in text:
        return "responsable_ventes"
    if "sales" in text:
        return "vente"
    return role.casefold().strip() if role else None

@dataclass
class Normalized:
    source_key:str
    contact:dict[str,Any]
    email:dict[str,Any]|None
    phone:dict[str,Any]|None
    organization:dict[str,Any]|None
    employment:dict[str,Any]|None
    evidence:dict[str,Any]
    reasons:list[str]

    def as_lookup(self) -> dict[str,Any]:
        return {
            "source_key":self.source_key,
            "contact_external_id":self.contact.get("external_id"),
            "email":(self.email or {}).get("value"),
            "organization_external_id":(self.organization or {}).get("external_id"),
            "organization_domain":(self.organization or {}).get("domain"),
        }

def normalize(payload:Any,source_key:str="unknown",profile:dict[str,Any]|None=None) -> Normalized:
    if not isinstance(payload,dict):
        payload={"value":payload}
    f=flatten(payload)

    def pick(target:str):
        value=profile_value(payload,profile,target)
        return value if value not in (None,"",[],{}) else first(f,target)

    email=normalize_email(pick("email"))
    first_name=normal_first(pick("first_name"))
    last_name=normal_last(pick("last_name"))
    if not first_name or not last_name:
        guessed_first,guessed_last=split_full_name(pick("full_name"))
        first_name=first_name or guessed_first
        last_name=last_name or guessed_last

    phone=_clean(pick("phone"))
    title=_clean(pick("job_title"))
    role=_clean(pick("job_role"))
    role_norm=normalize_role(title,role)
    company=_clean(pick("company_name"))
    raw_domain=pick("company_domain")
    domain=domain_from(raw_domain)
    if not domain and email:
        email_domain=domain_from(email)
        if email_domain and email_domain not in FREE_EMAIL_DOMAINS:
            domain=email_domain

    city=_clean(pick("city"))
    company_city=_clean(pick("company_city")) or city
    postal=_clean(pick("postal_code"))
    country=_clean(pick("country"))
    contact_ext=_clean(pick("external_contact_id"))
    company_ext=_clean(pick("external_company_id"))

    blob=text_blob(payload)
    extra_auto=set((profile or {}).get("automotive_terms") or [])
    extra_vo=set((profile or {}).get("vo_terms") or [])
    extra_sales=set((profile or {}).get("sales_terms") or [])
    automotive=has_any(blob,AUTO_TERMS|extra_auto)
    vo=has_any(blob,VO_TERMS|extra_vo)
    sales=has_any(blob,SALES_TERMS|extra_sales)
    professional_domain=bool(domain and domain not in FREE_EMAIL_DOMAINS)

    if vo:
        relevance="confirmed"
        relevance_conf=0.95
    elif automotive and (sales or professional_domain):
        relevance="likely"
        relevance_conf=0.80
    elif automotive:
        relevance="possible"
        relevance_conf=0.60
    else:
        relevance="unknown"
        relevance_conf=0.35

    reasons=[]
    if vo: reasons.append("vo_evidence")
    if automotive: reasons.append("automotive_evidence")
    if sales: reasons.append("sales_role_evidence")
    if professional_domain: reasons.append("professional_domain")
    if email: reasons.append("usable_email")
    if not email: reasons.append("missing_email")
    if not company: reasons.append("missing_company")
    if not first_name: reasons.append("missing_first_name")
    if not last_name: reasons.append("missing_last_name")
    if not company_city: reasons.append("missing_city")

    if email and relevance in {"likely","confirmed"}:
        qualification="qualified" if relevance=="confirmed" else "usable"
    else:
        qualification="to_enrich"

    contact={
        "external_id":contact_ext,
        "last_name":last_name,
        "first_name":first_name,
        "city":city.title() if city else None,
        "qualification_status":qualification,
        "vo_relevance":relevance,
        "confidence":relevance_conf,
    }
    email_obj=None
    if email:
        email_obj={
            "value":email,
            "kind":"personal_business" if professional_domain else "unknown",
            "deliverability_status":"unknown",
            "confidence":0.85 if professional_domain else 0.60,
        }
    phone_obj=None
    if phone:
        phone_obj={"value":phone,"kind":"unknown","confidence":0.60}

    org_obj=None
    if any([company,domain,company_ext,company_city]):
        org_obj={
            "external_id":company_ext,
            "display_name":company,
            "domain":domain,
            "city":company_city.title() if company_city else None,
            "postal_code":postal,
            "country_code":country.upper() if country and len(country)<=3 else "FR",
            "vo_relevance":relevance,
            "confidence":relevance_conf,
        }

    employment=None
    if title or role_norm or org_obj:
        employment={
            "job_title":title,
            "job_role":role_norm,
            "is_current":True,
            "confidence":0.80 if title or role_norm else 0.50,
        }

    return Normalized(
        source_key=source_key,
        contact=contact,
        email=email_obj,
        phone=phone_obj,
        organization=org_obj,
        employment=employment,
        evidence={
            "automotive":automotive,
            "vo":vo,
            "sales":sales,
            "professional_domain":professional_domain,
        },
        reasons=reasons,
    )
