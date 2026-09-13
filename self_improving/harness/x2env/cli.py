"""One foreground Harness command, one resume call, structured partial/failure output."""

import argparse
import json
import signal
import time
from pathlib import Path

from .contracts import InputMedia, RequestConstraints, X2EnvRequest
from .deployment import build_harness, load_deployment
from .preflight import check_deployment


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def _parser():
    parser = Parser(prog="x2env")
    parser.add_argument("--deployment", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "submit", "status", "resume", "package"):
        command = commands.add_parser(name)
        command.add_argument("--deployment", type=Path, default=argparse.SUPPRESS)
        if name == "check":
            continue
        if name == "submit":
            text = command.add_mutually_exclusive_group()
            text.add_argument("--text")
            text.add_argument("--text-file", type=Path)
            command.add_argument("--image", type=Path, action="append", default=[])
            command.add_argument("--video", type=Path)
            command.add_argument("--seed", type=int, required=True)
            command.add_argument(
                "--source", choices=("local", "web", "reconstruction"), action="append"
            )
            command.add_argument("--allow-cousin", action="store_true")
            command.add_argument("--idempotency-key", required=True)
            command.add_argument("--output", type=Path, required=True)
        else:
            command.add_argument("--workflow-id", required=True)
            if name == "package":
                command.add_argument("--output", type=Path, required=True)
    return parser


def main(argv=None):
    started = time.monotonic()
    handle = None
    snapshot = None
    stage = "arguments"
    previous_handler = None
    previous_timer = None
    expired = False

    def deadline(signum, frame):
        nonlocal expired
        expired = True
        raise KeyboardInterrupt("command_deadline")

    try:
        args = _parser().parse_args(argv)
        if args.deployment is None:
            raise ValueError("--deployment is required")
        stage = "deployment"
        config = load_deployment(args.deployment)
        if args.command == "check":
            report = check_deployment(config)
            report["command_wall_seconds"] = time.monotonic() - started
            print(json.dumps(report, sort_keys=True, allow_nan=False))
            return 0 if report["ok"] else 1

        def remaining():
            value = int(config.timeout_seconds - (time.monotonic() - started))
            if value < 1:
                deadline(None, None)
            return value

        previous_handler = signal.signal(signal.SIGALRM, deadline)
        previous_timer = signal.setitimer(
            signal.ITIMER_REAL, max(0.001, config.timeout_seconds - (time.monotonic() - started))
        )
        harness = build_harness(config)
        stage = args.command
        if args.command == "submit":
            text = args.text
            if args.text_file:
                if args.text_file.stat().st_size > 128 * 1024:
                    raise ValueError("text input too large")
                text = args.text_file.read_text()
            request = X2EnvRequest(
                text=text,
                images=tuple(InputMedia(path=str(p.absolute())) for p in args.image),
                video=InputMedia(path=str(args.video.absolute())) if args.video else None,
                seed=args.seed,
                allowed_sources=tuple(args.source or ("local", "web", "reconstruction")),
                constraints=RequestConstraints(allow_cousin=args.allow_cousin),
                idempotency_key=args.idempotency_key,
                output_dir=str(args.output.absolute()),
            )
            handle = harness.submit(request).workflow_id
            snapshot = harness.resume(handle, timeout=remaining())
        elif args.command == "resume":
            handle = args.workflow_id
            snapshot = harness.resume(handle, timeout=remaining())
        else:
            handle = args.workflow_id
            snapshot = harness.status(handle)
        body = {
            "workflow_id": handle,
            "status": snapshot.status,
            "snapshot": snapshot.model_dump(mode="json"),
        }
        if args.command == "package" or (
            args.command == "submit"
            and snapshot.status in {"succeeded", "failed", "blocked", "cancelled"}
        ):
            stage = "package"
            body["delivery"] = harness.package(
                handle, output=args.output.absolute(), reuse_existing=args.command == "submit"
            )
        if args.command == "submit" and args.allow_cousin:
            body["not_implemented"] = ["digital_cousin_selection"]
        code = (
            0
            if args.command in {"status", "package"} or snapshot.status == "succeeded"
            else 2
            if snapshot.status in {"blocked", "active"}
            else 1
        )
    except (ValueError, OSError, KeyError, ImportError, KeyboardInterrupt) as exc:
        if handle is not None and isinstance(exc, KeyboardInterrupt):
            snapshot = harness.status(handle)
        error = (
            "command_timeout"
            if expired
            else "command_interrupted"
            if isinstance(exc, KeyboardInterrupt)
            else "package_failed"
            if stage == "package"
            else "blocked_external_resource"
            if isinstance(exc, (FileNotFoundError, ImportError))
            else "invalid_request"
        )
        body = {
            "status": "failed",
            "error_code": error,
            "message": str(exc)[:2048],
            "stage": stage,
            "workflow_id": handle,
        }
        if snapshot:
            body["workflow_status"] = snapshot.status
        code = 1
    finally:
        if previous_handler is not None:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
            if previous_timer and previous_timer[0] > 0:
                signal.setitimer(
                    signal.ITIMER_REAL,
                    max(0.001, previous_timer[0] - (time.monotonic() - started)),
                    previous_timer[1],
                )
    body["command_wall_seconds"] = time.monotonic() - started
    print(json.dumps(body, sort_keys=True, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
