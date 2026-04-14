from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class StepResult:
    name: str
    success: bool
    exit_code: int
    command: list[str]
    payload: dict[str, Any] | None
    stdout: str
    stderr: str


def _extract_json(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None

    try:
        payload = json.loads(stripped)
        return payload if isinstance(payload, dict) else None
    except Exception:
        pass

    decoder = json.JSONDecoder()
    for start in range(len(stripped)):
        if stripped[start] != "{":
            continue
        try:
            payload, _end = decoder.raw_decode(stripped[start:])
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _run_json_step(
    *,
    name: str,
    command: list[str],
    cwd: Path,
    env_overrides: dict[str, str] | None = None,
) -> StepResult:
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)

    proc = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    payload = _extract_json(proc.stdout)
    success = proc.returncode == 0

    return StepResult(
        name=name,
        success=success,
        exit_code=proc.returncode,
        command=command,
        payload=payload,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def _step_to_dict(step: StepResult, output_mode: str) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": step.name,
        "success": step.success,
        "exit_code": step.exit_code,
        "command": " ".join(step.command),
        "payload": step.payload,
    }
    if output_mode == "full":
        base["stdout"] = step.stdout
        base["stderr"] = step.stderr
    return base


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run full reliability gate for phases 4-7")
    parser.add_argument("--random-samples", type=int, default=40)
    parser.add_argument("--baseline-canary-percent", type=int, default=100)
    parser.add_argument("--canary-percent", type=int, default=30)
    parser.add_argument("--logs-dir", default="logs")
    parser.add_argument("--output", choices=["summary", "full"], default="summary")
    parser.add_argument("--write-report", default="")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--skip-synonym-validate", action="store_true")
    parser.add_argument("--skip-user-situation", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    scripts_dir = repo_root / "scripts"
    logs_dir = repo_root / args.logs_dir
    logs_dir.mkdir(parents=True, exist_ok=True)

    python_exe = args.python

    baseline_telem = logs_dir / f"mapping_telemetry_gate_{args.baseline_canary_percent}.jsonl"
    canary_telem = logs_dir / f"mapping_telemetry_gate_{args.canary_percent}.jsonl"
    baseline_reg = logs_dir / f"regression_gate_{args.baseline_canary_percent}.json"
    canary_reg = logs_dir / f"regression_gate_{args.canary_percent}.json"

    for path in (baseline_telem, canary_telem, baseline_reg, canary_reg):
        if path.exists():
            path.unlink()

    base_env = {
        "MAPPING_ENABLE_CONFIDENCE_CALIBRATION": "true",
        "MAPPING_ENABLE_LEARNED_CONFIDENCE_CALIBRATION": "true",
        "MAPPING_ENABLE_SEMANTIC_FALLBACK": "true",
        "MAPPING_ENABLE_PRODUCT_FALLBACK": "true",
        "MAPPING_TELEMETRY_ENABLED": "true",
    }

    steps: list[StepResult] = []

    if not args.skip_synonym_validate:
        steps.append(
            _run_json_step(
                name="synonym_validate",
                command=[
                    python_exe,
                    str(scripts_dir / "synonym_governance.py"),
                    "validate",
                    "--synonyms",
                    "config/synonyms.json",
                    "--output",
                    "full",
                ],
                cwd=repo_root,
            )
        )

    baseline_step = _run_json_step(
        name="regression_baseline",
        command=[
            python_exe,
            str(scripts_dir / "relevance_regression.py"),
            "--random-samples",
            str(max(5, int(args.random_samples))),
            "--output",
            "full",
        ],
        cwd=repo_root,
        env_overrides={
            **base_env,
            "MAPPING_PHASE3_CANARY_PERCENT": str(args.baseline_canary_percent),
            "MAPPING_TELEMETRY_FILE": str(baseline_telem),
        },
    )
    baseline_reg.write_text(baseline_step.stdout if baseline_step.stdout else "{}", encoding="utf-8")
    steps.append(baseline_step)

    canary_step = _run_json_step(
        name="regression_canary",
        command=[
            python_exe,
            str(scripts_dir / "relevance_regression.py"),
            "--random-samples",
            str(max(5, int(args.random_samples))),
            "--output",
            "full",
        ],
        cwd=repo_root,
        env_overrides={
            **base_env,
            "MAPPING_PHASE3_CANARY_PERCENT": str(args.canary_percent),
            "MAPPING_TELEMETRY_FILE": str(canary_telem),
        },
    )
    canary_reg.write_text(canary_step.stdout if canary_step.stdout else "{}", encoding="utf-8")
    steps.append(canary_step)

    steps.append(
        _run_json_step(
            name="observability_baseline",
            command=[
                python_exe,
                str(scripts_dir / "observability_guard.py"),
                "--telemetry",
                str(baseline_telem),
                "--expected-canary-percent",
                str(args.baseline_canary_percent),
                "--output",
                "full",
            ],
            cwd=repo_root,
        )
    )

    steps.append(
        _run_json_step(
            name="observability_canary",
            command=[
                python_exe,
                str(scripts_dir / "observability_guard.py"),
                "--telemetry",
                str(canary_telem),
                "--baseline",
                str(baseline_telem),
                "--expected-canary-percent",
                str(args.canary_percent),
                "--output",
                "full",
            ],
            cwd=repo_root,
        )
    )

    canary_guard_step = _run_json_step(
        name="canary_guard",
        command=[
            python_exe,
            str(scripts_dir / "canary_guard.py"),
            "--baseline-telemetry",
            str(baseline_telem),
            "--canary-telemetry",
            str(canary_telem),
            "--baseline-regression",
            str(baseline_reg),
            "--canary-regression",
            str(canary_reg),
        ],
        cwd=repo_root,
    )
    steps.append(canary_guard_step)

    if not args.skip_user_situation:
        steps.append(
            _run_json_step(
                name="user_situation_validation",
                command=[
                    python_exe,
                    str(scripts_dir / "user_situation_validation.py"),
                    "--output",
                    "full",
                ],
                cwd=repo_root,
            )
        )

    steps.append(
        _run_json_step(
            name="continuous_quality_report",
            command=[
                python_exe,
                str(scripts_dir / "continuous_quality_report.py"),
                "--telemetry",
                str(baseline_telem),
                "--regression",
                str(baseline_reg),
                "--calibration-model",
                "config/mapping_confidence_calibration.json",
                "--output",
                "full",
            ],
            cwd=repo_root,
        )
    )

    step_dicts = [_step_to_dict(step, output_mode=args.output) for step in steps]
    failed_steps = [item for item in step_dicts if not item["success"]]

    canary_payload = canary_guard_step.payload or {}
    rollout_action = str(canary_payload.get("action") or "unknown")

    report: dict[str, Any] = {
        "overall_status": "pass" if not failed_steps else "fail",
        "failed_steps": [item["name"] for item in failed_steps],
        "failed_count": len(failed_steps),
        "rollout_action": rollout_action,
        "artifacts": {
            "baseline_regression": str(baseline_reg),
            "canary_regression": str(canary_reg),
            "baseline_telemetry": str(baseline_telem),
            "canary_telemetry": str(canary_telem),
        },
        "steps": step_dicts,
    }

    if args.output == "summary":
        for step in report["steps"]:
            step.pop("stdout", None)
            step.pop("stderr", None)
            payload = step.get("payload")
            if isinstance(payload, dict):
                compact_payload = dict(payload)
                # keep compact high-signal fields in summary mode
                for key in list(compact_payload.keys()):
                    if key in {"results", "checks", "sample_rows", "cadence", "baseline_metrics", "metrics"}:
                        compact_payload.pop(key, None)
                step["payload"] = compact_payload

    if args.write_report:
        output_path = Path(args.write_report)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2, ensure_ascii=True))
    raise SystemExit(1 if failed_steps else 0)


if __name__ == "__main__":
    main()
