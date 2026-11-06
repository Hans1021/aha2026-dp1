
import os
import re
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

from portkey_ai import Portkey

ROOT = Path(__file__).resolve().parents[1]
RTL = ROOT / "recovery/recovered_pins.v"
OUT = ROOT / "ai_logs/recovery"
REPORT = ROOT / "docs/ai_recovery_report.md"
MODEL = os.getenv("PORTKEY_MODEL", "@gpt-4o-mini/gpt-4o-mini")

OUT.mkdir(parents=True, exist_ok=True)
REPORT.parent.mkdir(parents=True, exist_ok=True)

original = RTL.read_text()
sha = hashlib.sha256(original.encode()).hexdigest()

# Strip comments before parsing logic expressions.
source = re.sub(r"/\*.*?\*/|//[^\n]*", "", original, flags=re.S)

assignments = dict(re.findall(
    r"\bassign\s+(\w+)\s*=\s*(.*?);", source, re.S
))

def nets(expr):
    return set(re.findall(r"\b(?:n\d+|pin_\d+)\b", expr or ""))

def constant(expr):
    seen = set()

    while expr is not None:
        expr = expr.strip()

        if expr in ("1'b0", "1'b1"):
            return int(expr[-1])

        if expr in seen or not re.fullmatch(r"\w+", expr):
            return None

        seen.add(expr)
        expr = assignments.get(expr)

    return None

# Extract physical RAM instances and their connections.
rams = []

for params, name, ports in re.findall(
    r"SB_RAM40_4K\s*#\((.*?)\)\s*(\w+)\s*\((.*?)\);",
    source, re.S
):
    init = dict(re.findall(
        r"\.INIT_([0-9A-F])\(256'h([0-9a-fA-F]+)\)",
        params
    ))

    def get_port(port):
        m = re.search(rf"\.{port}\((.*?)\)", ports, re.S)
        return m.group(1).strip() if m else None

    rams.append({
        "name": name,
        "init": init,
        "raddr": get_port("RADDR"),
        "rdata": get_port("RDATA"),
        "wclk": get_port("WCLK"),
        "we": get_port("WE"),
        "rclk": get_port("RCLK"),
    })

# Collect the sequential elements.
ffs = re.findall(
    r"\balways\s*@\((.*?)\)\s*"
    r"if\s*\(.*?\)\s*(\w+)\s*<=\s*(.*?);",
    source, re.S
)

# Build a dependency graph.
consumers = defaultdict(set)

for dst, expr in assignments.items():
    for sig in nets(expr):
        consumers[sig].add(dst)

for _, dst, expr in ffs:
    for sig in nets(expr):
        consumers[sig].add(dst)

ff_names = {dst for _, dst, _ in ffs}

def trace_outputs(seeds, depth=12):
    reached = set(seeds)
    frontier = set(seeds)

    for _ in range(depth):
        next_frontier = set()

        for sig in frontier:
            next_frontier |= consumers.get(sig, set())

        next_frontier -= reached
        reached |= next_frontier
        frontier = next_frontier

        if not frontier:
            break

    return {
        "registers_reached": sorted(reached & ff_names),
        "outputs_reached": sorted(
            reached & {"pin_31", "pin_38", "pin_23"}
        ),
        "signal_count": len(reached),
    }

init_data = [
    tuple(r["init"].get(f"{i:X}") for i in range(16))
    for r in rams
]

checks = {
    "ram_count": len(rams),
    "ram_initializations_identical": (
        bool(rams)
        and all(x == init_data[0] for x in init_data)
        and all(v is not None for v in init_data[0])
    ),
    "ram_read_addresses_distinct": (
        bool(rams)
        and all(r["raddr"] is not None for r in rams)
        and len({r["raddr"] for r in rams}) == len(rams)
    ),
    "all_write_clocks_constant_zero": (
        bool(rams)
        and all(constant(r["wclk"]) == 0 for r in rams)
    ),
    "all_write_enables_constant_one": (
        bool(rams)
        and all(constant(r["we"]) == 1 for r in rams)
    ),
    "busy_directly_aliases_led": (
        assignments.get("pin_23") == "pin_38"
    ),
    "flip_flop_count": len(ffs),
}

ram_evidence = []

for r in rams:
    outputs = {
        s for s in nets(r["rdata"])
        if s.startswith("n")
    }

    ram_evidence.append({
        "name": r["name"],
        "raddr": r["raddr"],
        "rdata": r["rdata"],
        "wclk": r["wclk"],
        "wclk_value": constant(r["wclk"]),
        "we": r["we"],
        "we_value": constant(r["we"]),
        "downstream": trace_outputs(outputs),
    })

evidence = {
    "source_sha256": sha,
    "checks": checks,
    "rom_connections": ram_evidence,
    "verified_port_names": [
        "pin_36", "pin_25", "pin_28",
        "pin_32", "pin_34", "pin_27",
        "pin_31", "pin_38", "pin_23"
    ],
    "port_mapping": {
        "pin_36": "SCK",
        "pin_25": "ENC_DEC (inferred)",
        "pin_28": "NORM_CS_N",
        "pin_32": "MOSI",
        "pin_34": "RST_N",
        "pin_27": "START",
        "pin_31": "MISO",
        "pin_38": "ICE_LED",
        "pin_23": "BUSY"
    },
    "representative_logic": {
        name: assignments.get(name)
        for name in (
            "n5", "n2315", "n466", "n1925",
            "n600", "n612", "pin_23", "pin_38"
        )
    }
}

(OUT / "deterministic_evidence.json").write_text(
    json.dumps(evidence, indent=2)
)

client = Portkey(
    base_url="https://ai-gateway.apps.cloud.rt.nyu.edu/v1",
    api_key=os.environ["NYU_AI_API_KEY"],
)

SYSTEM = """
You are an FPGA reverse-engineering analyst.

Analyze only the supplied evidence.
Distinguish static facts from functional hypotheses.
Do not invent signals or claim simulation has occurred.
A valid signal name alone does not prove a claim.
Use exact external port names (pin_XX).
All conclusions must be evidence-grounded.
"""

def call_api(stage, prompt):
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": prompt}
    ]

    record = {
        "stage": stage,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        "source_sha256": sha,
        "request": {
            "model": MODEL,
            "messages": messages,
            "max_tokens": 2500
        }
    }

    path = OUT / f"{stage}.json"
    path.write_text(json.dumps(record, indent=2))

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            max_tokens=2500
        )
        record["response"] = response.model_dump()
        answer = response.choices[0].message.content or ""
        record["text"] = answer
    except Exception as exc:
        record["error"] = repr(exc)
        path.write_text(json.dumps(record, indent=2))
        with (OUT / "history.jsonl").open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")
        raise

    path.write_text(json.dumps(record, indent=2, default=str))

    # Preserve complete API history in one directory.
    with (OUT / "history.jsonl").open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")

    print(f"Completed: {stage}")
    return answer

analysis = call_api(
    "01_structured_analyst",
    """
Analyze this recovered FPGA architecture.

Return a JSON object with:
- confirmed_facts: claims, evidence keys, exact signals
- hypotheses: architectural interpretations, confidence,
  exact signals and falsification tests
- next_steps: two high-information-gain experiments

Focus especially on:
1. Four identical ROMs and their read addresses.
2. ROM outputs feeding registers and other ROM addresses.
3. How this suggests a multi-byte iterative datapath.
4. What remains unknown about the cipher.
5. How to trace mode selection and final output.

Do not repeat obvious facts as hypotheses.
Do not infer a particular cipher without evidence.

EVIDENCE:
"""
    + json.dumps(evidence, indent=2)
)

# Extract JSON from the model response.
def parse_response(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"parse_error": True, "raw_text": text}

claims = parse_response(analysis)

# Validate signal references and explicitly check evidence keys.
valid_ids = set(re.findall(r"\b[A-Za-z_]\w*\b", source))
results = []

for category in ("confirmed_facts", "hypotheses"):
    for claim in claims.get(category, []):
        if not isinstance(claim, dict):
            continue

        names = claim.get("signals", [])
        if not isinstance(names, list):
            names = []

        unknown = [
            name for name in names
            if not isinstance(name, str) or name not in valid_ids
        ]

        key = claim.get("evidence_key")
        check_name = (
            key.removeprefix("checks.")
            if isinstance(key, str) and key.startswith("checks.")
            else None
        )
        check_value = checks.get(check_name) if check_name else None

        results.append({
            "category": category,
            "claim": claim.get("claim"),
            "unknown_signals": unknown,
            "evidence_key": key,
            "check_value": check_value,
            "failed_boolean_check": check_value is False,
            "note": "Structural checks cannot prove functional meaning."
        })

validation = {
    "claims": claims,
    "checks": checks,
    "claim_validation": results
}

(OUT / "02_claim_validation.json").write_text(
    json.dumps(validation, indent=2)
)

review = call_api(
    "03_evidence_reviewer",
    """
You are reviewing an FPGA analysis, not just summarizing it.

Inspect the model claims and automated verification.

Explicitly:
1. Reject unsupported or incorrect conclusions.
2. Distinguish identical address expressions from
   functionally equivalent addresses.
3. Confirm ROM write-clock interpretation from
   resolved constants, not guesses.
4. Identify claims using incorrect net names.
5. Explain whether ROM outputs participate
   in a registered feedback structure.
6. Propose two concrete experiments toward
   understanding the actual cryptographic datapath.
7. State what is still unknown.

Return a concise Markdown technical assessment.

ANALYST:
"""
    + analysis
    + "\n\nVALIDATION:\n"
    + json.dumps(validation, indent=2)
    + "\n\nORIGINAL EVIDENCE:\n"
    + json.dumps(evidence, indent=2)
)

REPORT.write_text(
    "# AI-Assisted FPGA Reverse Engineering\n\n"
    f"Model: `{MODEL}`\n\n"
    f"Netlist SHA256: `{sha}`\n\n"
    "## Verified structural facts\n\n"
    "```json\n"
    + json.dumps(checks, indent=2)
    + "\n```\n\n"
    "## Analyst findings\n\n"
    + analysis
    + "\n\n## Independent review\n\n"
    + review
    + "\n\n## Automated claim validation\n\n"
    "```json\n"
    + json.dumps(results, indent=2)
    + "\n```\n"
)

print("\nComplete.")
print("Report:", REPORT)
print("API logs:", OUT)
