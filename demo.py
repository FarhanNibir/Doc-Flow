"""End-to-end CLI demo:  python demo.py [--provider mock|openai|gemini] [--long]

Runs: intake -> AI draft -> layout check -> QA -> review -> approve -> sign/seal -> final PDF.
Output PDFs and the audit log go to ./out
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from core import pipeline as P
from core.ai import get_provider

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = {
    "organization": "Northfield Business Center",
    "asset": "Air Conditioner — Unit AC-204",
    "inspection_date": "2026-11-06",
    "inspector": "Arif Rahman",
    "findings": "Air filter was dusty. Cooling performance was slightly reduced. "
                "No unusual noise or leakage observed.",
    "recommended_action": "Clean air filter, check thermostat calibration, and recheck "
                          "cooling performance after servicing.",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default=None)
    ap.add_argument("--long", action="store_true", help="use very long findings to demo overflow")
    a = ap.parse_args()

    out = os.path.join(HERE, "out")
    os.makedirs(out, exist_ok=True)
    t = P.load_template(os.path.join(HERE, "templates", "inspection_report.json"))
    data = dict(SAMPLE)
    if a.long:
        data["findings"] = " ".join(f"Observation {i}: panel section {i} checked, no issue noted."
                                    for i in range(1, 41))
    provider = get_provider(a.provider)

    req = P.start_request(t, "DEMO-2026-014", data, provider)
    print("state:", req.state)
    rep = P.layout_check(t, req, out)
    print("layout:", "ok" if rep.ok else "CLIPPED", "| pages:", rep.pages, "| warnings:", rep.warnings)
    P.internal_qa_pass(req, "staff:sara")
    req.approve("Mahmud Karim")
    path, digest = P.sign_and_finalize(t, req, out, "Mahmud Karim")
    P.deliver(req)
    print("final:", path, "\nsha256:", digest, "\naudit intact:", req.verify_audit())
    with open(os.path.join(out, "audit.json"), "w") as f:
        json.dump(req.audit, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
