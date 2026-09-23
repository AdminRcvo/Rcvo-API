import tempfile
import unittest
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))

from agent import AgentConfig,SourcingAgent
from contracts import CandidateLookup,RawItem
from fake_gateways import MemoryRawGateway,MemoryReferenceGateway
from normalization import normalize
from profiles import ProfileRegistry
from state import AgentState

def item(i,payload,source="apollo",batch="batch-a"):
    return RawItem(
        id=i,batch_id=1,raw_batch_uuid=batch,source_key=source,
        source_record_id=f"{source}-{i}",source_row_number=i,
        captured_at="2026-09-23T12:00:00.000Z",payload=payload
    )

class ConflictReference(MemoryReferenceGateway):
    def lookup(self,normalized):
        return CandidateLookup(
            contact_rcvo_id="CNT-A",
            methods=["external_id","exact_email"],
            conflicts=["strong contact keys resolve to different contacts"],
        )

class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.state=AgentState(Path(self.tmp.name)/"state.sqlite")
    def tearDown(self):
        self.tmp.cleanup()

    def test_normalizes_common_provider_shapes(self):
        n=normalize({
            "person":{
                "first_name":"jEAN",
                "last_name":"dupont",
                "work_email":"JEAN.DUPONT@GARAGE-X.FR",
                "title":"Responsable VO"
            },
            "organization":{
                "company_name":"Garage X SAS",
                "website":"https://www.garage-x.fr/",
                "city":"BORDEAUX"
            },
            "industry":"automobile occasion"
        },"apollo")
        self.assertEqual(n.contact["first_name"],"Jean")
        self.assertEqual(n.contact["last_name"],"DUPONT")
        self.assertEqual(n.email["value"],"jean.dupont@garage-x.fr")
        self.assertEqual(n.organization["domain"],"garage-x.fr")
        self.assertEqual(n.contact["vo_relevance"],"confirmed")
        self.assertEqual(n.contact["qualification_status"],"qualified")
        self.assertEqual(n.employment["job_role"],"responsable_vo")

    def test_incomplete_contact_is_kept_for_enrichment(self):
        n=normalize({
            "company":"Garage Martin",
            "city":"Dax",
            "activity":"automobile"
        },"source-x")
        self.assertEqual(n.contact["qualification_status"],"to_enrich")
        self.assertIn("missing_email",n.reasons)
        self.assertEqual(n.organization["display_name"],"Garage Martin")

    def test_provider_profile_maps_nonstandard_fields(self):
        profile={
            "source_key":"weird-provider",
            "fields":{
                "email":["person.contact.work"],
                "first_name":["person.identity.given"],
                "last_name":["person.identity.family"],
                "company_name":["account.label"],
                "company_domain":["account.webhost"]
            },
            "vo_terms":["preowned-unit"]
        }
        n=normalize({
            "person":{
                "contact":{"work":"X@DEALER.FR"},
                "identity":{"given":"alice","family":"martin"}
            },
            "account":{"label":"Dealer Test","webhost":"dealer.fr"},
            "segment":"preowned-unit"
        },"weird-provider",profile)
        self.assertEqual(n.email["value"],"x@dealer.fr")
        self.assertEqual(n.contact["first_name"],"Alice")
        self.assertEqual(n.contact["last_name"],"MARTIN")
        self.assertEqual(n.organization["display_name"],"Dealer Test")
        self.assertEqual(n.contact["vo_relevance"],"confirmed")

    def test_production_promotes_and_acks(self):
        raw=MemoryRawGateway([item(1,{
            "first_name":"Jean","last_name":"Dupont",
            "email":"jean.dupont@garage-x.fr",
            "company":"Garage X","website":"garage-x.fr",
            "job_title":"Responsable VO","sector":"automobile occasion"
        })])
        ref=MemoryReferenceGateway()
        agent=SourcingAgent(raw,ref,self.state,AgentConfig(
            mode="production",claim_size=50,worker_id="test-worker"
        ))
        result=agent.process_batch()
        self.assertEqual(result["claimed"],1)
        self.assertEqual(result["promoted"],1)
        self.assertEqual(raw.state[1],"processed")
        self.assertEqual(len(ref.promotions),1)
        self.assertEqual(ref.promotions[0]["raw_ref"]["source_key"],"apollo")
        self.assertEqual(ref.promotions[0]["raw_ref"]["raw_batch_uuid"],"batch-a")

    def test_simulation_does_not_consume_raw_or_write_reference(self):
        raw=MemoryRawGateway([item(1,{
            "email":"contact@garage.fr",
            "company":"Garage Test",
            "industry":"automobile occasion"
        })])
        ref=MemoryReferenceGateway()
        agent=SourcingAgent(raw,ref,self.state,AgentConfig(
            mode="simulation",worker_id="sim-worker"
        ))
        result=agent.process_batch()
        self.assertEqual(result["promoted"],1)
        self.assertEqual(raw.state[1],"pending")
        self.assertEqual(len(ref.promotions),0)

    def test_conflicting_strong_keys_are_rejected_not_merged(self):
        raw=MemoryRawGateway([item(1,{
            "email":"contact@garage.fr",
            "company":"Garage Test",
            "industry":"automobile occasion"
        })])
        ref=ConflictReference()
        agent=SourcingAgent(raw,ref,self.state,AgentConfig(
            mode="production",worker_id="conflict-worker"
        ))
        result=agent.process_batch()
        self.assertEqual(result["rejected"],1)
        self.assertEqual(raw.state[1],"rejected")
        self.assertEqual(len(ref.promotions),0)

    def test_same_email_on_second_source_is_enrichment(self):
        ref=MemoryReferenceGateway()
        raw1=MemoryRawGateway([item(1,{
            "email":"jean@garage.fr",
            "company":"Garage X",
            "industry":"automobile occasion"
        },source="apollo",batch="a")])
        a1=SourcingAgent(raw1,ref,self.state,AgentConfig(
            mode="production",worker_id="worker-a"
        ))
        a1.process_batch()

        raw2=MemoryRawGateway([item(2,{
            "first_name":"Jean","last_name":"Dupont",
            "email":"JEAN@GARAGE.FR",
            "job_title":"Vendeur VO",
            "company":"Garage X"
        },source="hunter",batch="h")])
        a2=SourcingAgent(raw2,ref,self.state,AgentConfig(
            mode="production",worker_id="worker-b"
        ))
        result=a2.process_batch()
        self.assertEqual(result["enriched"],1)
        self.assertEqual(len(ref.contacts_by_email),1)
        self.assertEqual(len(ref.promotions),2)
        self.assertIn("contact_match_rcvo_id",ref.promotions[1])

    def test_agent_operational_state_contains_no_contact_payload_copy(self):
        raw=MemoryRawGateway([item(1,{
            "email":"secret.person@garage.fr",
            "company":"Garage X",
            "industry":"automobile occasion"
        })])
        ref=MemoryReferenceGateway()
        SourcingAgent(raw,ref,self.state,AgentConfig(
            mode="production",worker_id="privacy-worker"
        )).process_batch()
        with self.state.connect() as conn:
            decision=conn.execute("SELECT * FROM sourcing_decisions").fetchone()
            raw_dump=" ".join(str(v) for v in dict(decision).values())
        self.assertNotIn("secret.person@garage.fr",raw_dump)

if __name__=="__main__":
    unittest.main()
