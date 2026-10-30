# SPDX-License-Identifier: MIT OR Apache-2.0
"""Measure the real catalog API only in a fresh disposable Compose namespace.

This is a bounded catalog measurement, not the M2 mixed-workload/24-hour soak.
Raw requests, failures, hardware, dataset cardinalities and plans remain explicit.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from tools.runtime_proof import API, ProbeFailure, Proof, require, run

QUERIES = (
    "/api/v1/postings?limit=20",
    "/api/v1/postings?limit=20&q=rareprobe",
    "/api/v1/postings?limit=20&q=Linux+SQL&location=London&status=open",
    "/api/v1/postings?limit=20&disposition=saved",
    "/api/v1/postings?limit=20&q=Catalog+probe+seed",
)

PLAN_CODE = """
import json,sys
from urllib.parse import parse_qs,urlsplit
from uuid import UUID
from sqlalchemy import event
from bonafides.catalog import CatalogQuery,list_postings
from bonafides.config import Settings
from bonafides.db import Database
settings=Settings.load()
db=Database(settings)
captured=[]
def capture(conn,cursor,statement,parameters,context,executemany):
 if statement.lstrip().upper().startswith('SELECT') and 'bonafides.job_postings' in statement:
  captured.append((statement,parameters))
event.listen(db.engine,'before_cursor_execute',capture)
plans=[]
with db.transaction() as conn:
 for url in json.loads(sys.argv[2]):
  args={k:v[0] for k,v in parse_qs(urlsplit(url).query).items()}
  args['limit']=int(args['limit'])
  captured.clear()
  list_postings(conn,UUID(sys.argv[1]),settings.keyring,CatalogQuery(**args))
  assert captured
  statement,parameters=captured[-1]
  result=conn.exec_driver_sql('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+statement,parameters).scalar_one()
  plans.append({'query':args,'statement':statement,'plan':result})
print(json.dumps(plans))
db.close()
"""


def seed(proof: Proof, source_id: str, owner_id: str, previous: int, count: int) -> None:
    # Identifiers originate from authenticated API UUID responses, never operator SQL.
    from uuid import UUID

    owner, source = str(UUID(owner_id)), str(UUID(source_id))
    proof.sql(f"""
INSERT INTO bonafides.job_postings
(owner_id,id,title,company,location_text,description_text,dedupe_key,source_config_id,
 external_id,content_sha256,last_observed_at,created_at,status)
SELECT '{owner}'::uuid,md5('bonafides-catalog-load:'||i)::uuid,
 'Synthetic operations engineer '||i,'Synthetic employer '||(i%100),
 CASE WHEN i%7=0 THEN 'London' ELSE 'Remote within synthetic region '||(i%10) END,
 'Linux SQL operational procedures. '||CASE WHEN i%101=0 THEN 'rareprobe ' ELSE '' END||repeat('Retained original synthetic posting. ',20),
 'catalog-load:'||i,'{source}'::uuid,'catalog-load:'||i,repeat('a',64),
 clock_timestamp()-i*interval '1 second','2026-01-01 00:00:00+00'::timestamptz,
 CASE WHEN i%11=0 THEN 'closed' WHEN i%13=0 THEN 'unknown' ELSE 'open' END
FROM generate_series({max(1, previous)}, {count - 1}) i;
INSERT INTO bonafides.job_source_observations
(owner_id,posting_id,source_config_id,external_id,content_sha256,source_url,snapshot)
SELECT owner_id,id,source_config_id,external_id,content_sha256,'manual:synthetic-load',
 jsonb_build_object('title',title,'description_text',description_text)
FROM bonafides.job_postings p WHERE owner_id='{owner}' AND dedupe_key LIKE 'catalog-load:%'
AND NOT EXISTS(SELECT 1 FROM bonafides.job_source_observations o WHERE o.owner_id=p.owner_id AND o.posting_id=p.id);
INSERT INTO bonafides.saved_opportunities(owner_id,posting_id)
SELECT owner_id,id FROM bonafides.job_postings p WHERE owner_id='{owner}'
AND dedupe_key LIKE 'catalog-load:%' AND external_id ~ '0$'
AND NOT EXISTS(SELECT 1 FROM bonafides.saved_opportunities s WHERE s.owner_id=p.owner_id AND s.posting_id=p.id);
ANALYZE bonafides.job_postings;
ANALYZE bonafides.job_source_observations;
ANALYZE bonafides.saved_opportunities;
""")
    actual = int(proof.sql("SELECT count(*) FROM bonafides.job_postings"))
    require(actual == count, "catalog_dataset_cardinality_mismatch")


def percentile(values: list[float], quantile: float) -> float:
    require(bool(values), "empty_latency_denominator")
    return sorted(values)[max(0, math.ceil(len(values) * quantile) - 1)]


def load(proof: Proof, count: int, rate: int, requests: int) -> list[dict[str, Any]]:
    def one(index: int, scheduled: float) -> dict[str, Any]:
        client = API(proof.origin)
        client.cookie, client.csrf = dict(proof.api.cookie), proof.api.csrf
        started = time.monotonic()
        query_index = index % len(QUERIES)
        result: dict[str, Any] = {
            "cardinality": count,
            "rate_per_second": rate,
            "index": index,
            "query_index": query_index,
            "schedule_lag_ms": (started - scheduled) * 1000,
            "ok": False,
        }
        try:
            page, _, raw = client.request("GET", QUERIES[query_index])
            require(isinstance(page.get("items"), list), "catalog_missing_items")
            require(len(page["items"]) <= 20, "catalog_page_exceeds_bound")
            require(bool(page["items"]), "positive_catalog_query_returned_empty")
            result.update(ok=True, rows=len(page["items"]), response_bytes=len(raw))
        except (ProbeFailure, OSError, ValueError) as exc:
            # Never retain body/query values/cookies/credentials in diagnostics.
            result["failure_type"] = type(exc).__name__
        result["latency_ms"] = (time.monotonic() - started) * 1000
        return result

    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = []
        for index in range(requests):
            scheduled = start + index / rate
            delay = scheduled - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            futures.append(pool.submit(one, index, scheduled))
        return [future.result() for future in futures]


def measure(proof: Proof, requests: int) -> None:
    session, _, _ = proof.api.request("GET", "/api/v1/session")
    seed_posting, _, _ = proof.api.request(
        "POST",
        "/api/v1/postings",
        {
            "title": "Catalog probe seed",
            "company": "Synthetic employer",
            "description_text": "Linux SQL synthetic seed",
        },
        expected=(201,),
        headers={"Idempotency-Key": secrets.token_hex(16)},
    )
    records: list[dict[str, Any]] = []
    previous = 0
    for count in (1000, 10000, 100000):
        seed(proof, seed_posting["source_config_id"], session["owner_id"], previous, count)
        previous = count
        plans = json.loads(proof.app_python(PLAN_CODE, session["owner_id"], json.dumps(QUERIES)))
        (proof.directory / f"plans-{count}.json").write_text(json.dumps(plans, indent=2) + "\n")
        if count == 100000:
            require(
                "job_postings_fts_idx" in json.dumps(plans[-1]),
                "selective_search_did_not_use_fts_index",
            )
        for query in QUERIES:
            for _ in range(5):
                proof.api.request("GET", query)
        for rate in (1, 5, 20):
            batch = load(proof, count, rate, requests)
            records.extend(batch)
            (proof.directory / "requests.json").write_text(json.dumps(records, indent=2) + "\n")
            latency = [row["latency_ms"] for row in batch]
            proof.evidence.setdefault("loads", []).append(
                {
                    "cardinality": count,
                    "requests": len(batch),
                    "successes": sum(row["ok"] for row in batch),
                    "rate_per_second": rate,
                    "p50_ms": percentile(latency, 0.5),
                    "p95_ms": percentile(latency, 0.95),
                    "p99_ms": percentile(latency, 0.99),
                    "denominator": "all scheduled HTTP requests, full response receipt, failures retained",
                    "warmup": "five requests per query; warm process/cache, not OS-cold",
                }
            )
            (proof.directory / "receipt.json").write_text(
                json.dumps(proof.evidence, indent=2) + "\n"
            )
            require(all(row["ok"] for row in batch), "catalog_load_request_failure")
        proof.checked(f"catalog_{count}_actual_http_load")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--confirm-disposable", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--requests-per-rate", type=int, default=120)
    args = parser.parse_args()
    require(
        args.project == args.confirm_disposable
        and re.fullmatch(r"bonafides-proof-[a-z0-9-]{1,32}", args.project) is not None,
        "disposable_confirmation_required",
    )
    require(
        1 <= args.requests_per_rate <= 100000 and 1024 <= args.port <= 65535, "invalid_load_bounds"
    )
    directory = Path(".local") / args.project
    proof = Proof(args.project, args.port, "linux/arm64", args.image, directory)
    proof.evidence.update(
        {
            "protocol": "OL008 bounded retained-catalog HTTP load v2",
            "host": platform.platform(),
            "machine": platform.machine(),
            "dataset_seed": "bonafides-catalog-load:<1..N-1>",
            "protocol_revision": (
                "v1 incorrectly required GIN for the 1%-selective rareprobe query; "
                "v2 retains that query and unchanged dataset, adds the existing "
                "single-result seed conjunction, and checks GIN only for that probe"
            ),
            "queries": list(QUERIES),
            "limits": "No model/source calls, no mixed-write contention or M2 soak claim",
            "image": json.loads(run(["docker", "image", "inspect", args.image]))[0]["Id"],
            "docker": json.loads(run(["docker", "info", "--format", "{{json .}}"])),
        }
    )
    try:
        proof.start(False)
        proof.identity()
        measure(proof, args.requests_per_rate)
        proof.evidence["passed"] = True
    except (ProbeFailure, OSError, ValueError) as exc:
        proof.evidence.update(passed=False, failure_type=type(exc).__name__)
        raise
    finally:
        if proof.created:
            proof.evidence["duration_seconds"] = time.monotonic() - proof.started
            (directory / "receipt.json").write_text(json.dumps(proof.evidence, indent=2) + "\n")
            proof.compose("stop", timeout=180)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
