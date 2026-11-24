from __future__ import annotations

import argparse
import json
import shutil
import sys
from importlib import resources
from pathlib import Path

from .gate import evaluate_gate
from .policy import DEFAULT_POLICY_PATH, Policy
from .risk import classify
from .validate import (
    ValidationFailure,
    validate_evidence,
    validate_release,
    validate_review,
    validate_security,
)


def _default_artifacts_dir(pr: str) -> Path:
    return Path(".prctl") / "artifacts" / str(pr)


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _copy_asset(name: str, target: Path) -> None:
    root = resources.files("prctl").joinpath("assets")
    source = root.joinpath(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, target.open("wb") as dst:
        shutil.copyfileobj(src, dst)


def cmd_init(args: argparse.Namespace) -> int:
    base = args.dir / ".prctl"
    policy_path = base / "policy.yml"
    schema_dir = base / "schemas"
    artifacts_dir = base / "artifacts"

    if policy_path.exists() and not args.force:
        print(f"policy already exists: {policy_path} (use --force to overwrite)")
    else:
        _copy_asset("policy.yml", policy_path)
        print(f"wrote {policy_path}")

    schema_dir.mkdir(parents=True, exist_ok=True)
    schema_root = resources.files("prctl").joinpath("assets").joinpath("schemas")
    for entry in schema_root.iterdir():
        if not entry.name.endswith(".schema.json"):
            continue
        target = schema_dir / entry.name
        if target.exists() and not args.force:
            continue
        with entry.open("rb") as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    print(f"initialized {base}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    policy = _policy_from_args(args)
    evidence = _load_json(args.evidence)
    validate_evidence(evidence, policy)
    target = args.artifacts_dir / "evidence.json"
    _write_json(target, evidence)
    print(f"evidence PASS -> {target}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    policy = _policy_from_args(args)
    report = _load_json(args.report)
    target = args.artifacts_dir / f"review-{report['round']}.json"
    existing = sorted(
        (
            path
            for path in args.artifacts_dir.glob("review-*.json")
            if path != target
            and int(path.stem.split("-")[-1]) < report["round"]
        ),
        key=lambda path: int(path.stem.split("-")[-1]),
    )
    previous = [_load_json(path) for path in existing]
    validate_review(report, policy, previous)
    _write_json(target, report)
    print(f"review round {report['round']} PASS -> {target}")
    return 0


def cmd_security(args: argparse.Namespace) -> int:
    policy = _policy_from_args(args)
    report = _load_json(args.report)
    validate_security(report, policy)
    target = args.artifacts_dir / "security.json"
    _write_json(target, report)
    print(f"security PASS -> {target}")
    return 0


def cmd_risk(args: argparse.Namespace) -> int:
    policy = _policy_from_args(args)
    files = list(args.files)
    if args.files_from:
        files += [
            line.strip()
            for line in args.files_from.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    result = classify(sorted(set(files)), policy)
    target = args.output or (args.artifacts_dir / "risk.json")
    _write_json(target, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    risk_path = args.artifacts_dir / "risk.json"
    if not risk_path.exists():
        raise ValidationFailure("risk.json is missing; run `prctl risk` first")
    risk = _load_json(risk_path)
    required = int(risk["approvals"])
    approvals_path = args.artifacts_dir / "approvals.json"
    approvers: list[str] = []
    if approvals_path.exists():
        data = _load_json(approvals_path)
        if isinstance(data, dict):
            approvers = [str(item) for item in data.get("approvers", [])]
        elif isinstance(data, list):
            approvers = [str(item) for item in data]
    if args.by not in approvers:
        approvers.append(args.by)
    _write_json(approvals_path, {"approvers": approvers})
    print(f"approvals: {len(approvers)}/{required} ({', '.join(approvers)})")
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    policy = _policy_from_args(args)
    result = evaluate_gate(args.artifacts_dir, policy, args.approvals)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{'CHECK':<12} {'STATUS':<8} DETAIL")
        for check in result["checks"]:
            status = "PASS" if check["passed"] else "FAIL"
            print(f"{check['name']:<12} {status:<8} {check['detail']}")
        print(f"\nOVERALL: {result['overall']}  STATE: {result['state']}")
    return 0 if result["overall"] == "PASS" else 1


def cmd_status(args: argparse.Namespace) -> int:
    policy = _policy_from_args(args)
    result = evaluate_gate(args.artifacts_dir, policy, args.approvals)
    print(f"PR {args.pr}: {result['state']} ({result['overall']})")
    for check in result["checks"]:
        status = "PASS" if check["passed"] else "FAIL"
        print(f"  {check['name']}: {status} - {check['detail']}")
    return 0 if result["overall"] == "PASS" else 1


def cmd_release(args: argparse.Namespace) -> int:
    policy = _policy_from_args(args)
    plan = _load_json(args.plan)
    validate_release(plan, policy)
    target = args.artifacts_dir / "release.json"
    _write_json(target, plan)
    print(f"release plan PASS -> {target}")
    return 0


def _policy_from_args(args: argparse.Namespace) -> Policy:
    """显式指定的 --policy 必须存在；只有默认路径才允许回退内置规则。"""
    explicit = Path(args.policy) != DEFAULT_POLICY_PATH
    return Policy.load(args.policy, strict=explicit)


def _add_common(parser: argparse.ArgumentParser, pr_required: bool = True) -> None:
    if pr_required:
        parser.add_argument("--pr", required=True, help="PR number or identifier")
    else:
        parser.add_argument("--pr", default=None, help="PR number or identifier")
    parser.add_argument("--artifacts-dir", type=Path, default=None)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prctl",
        description="PR gate for AI-assisted projects: validate artifacts, not promises.",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create .prctl/policy.yml and schemas")
    init.add_argument("--dir", type=Path, default=Path("."))
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    verify = sub.add_parser("verify", help="validate an evidence pack")
    _add_common(verify)
    verify.add_argument("--evidence", type=Path, required=True)
    verify.set_defaults(func=cmd_verify)

    review = sub.add_parser("review", help="validate a review report")
    _add_common(review)
    review.add_argument("--report", type=Path, required=True)
    review.set_defaults(func=cmd_review)

    security = sub.add_parser("security", help="validate a security report")
    _add_common(security)
    security.add_argument("--report", type=Path, required=True)
    security.set_defaults(func=cmd_security)

    risk = sub.add_parser("risk", help="classify changed files")
    _add_common(risk, pr_required=False)
    risk.add_argument("--files", nargs="*", default=[])
    risk.add_argument("--files-from", type=Path)
    risk.add_argument("--output", type=Path)
    risk.set_defaults(func=cmd_risk)

    approve = sub.add_parser("approve", help="record an approver")
    _add_common(approve)
    approve.add_argument("--by", required=True)
    approve.set_defaults(func=cmd_approve)

    gate = sub.add_parser("gate", help="check all gates")
    _add_common(gate)
    gate.add_argument("--approvals", type=int, default=None)
    gate.add_argument("--json", action="store_true")
    gate.set_defaults(func=cmd_gate)

    status = sub.add_parser("status", help="show the computed state")
    _add_common(status)
    status.add_argument("--approvals", type=int, default=None)
    status.set_defaults(func=cmd_status)

    release = sub.add_parser("release", help="validate a release plan")
    _add_common(release)
    release.add_argument("--plan", type=Path, required=True)
    release.set_defaults(func=cmd_release)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "artifacts_dir", None) is None:
        if getattr(args, "pr", None):
            args.artifacts_dir = _default_artifacts_dir(args.pr)
        else:
            args.artifacts_dir = Path(".prctl") / "artifacts"
    try:
        return args.func(args)
    except (ValidationFailure, json.JSONDecodeError, OSError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
