"""svkg command line.

  svkg fetch-legislation       download provision wording from legislation.govt.nz (then re-extract)
  svkg extract                 parse the spec .docx into the Appendix A files
  svkg check                   diff the files against the document (non-zero exit on mismatch)
  svkg load                    (re)load the studentvisa graph; idempotent
  svkg build                   extract + check + load
  svkg assess app.json         assess one synthetic application (writes assessment nodes + audit log)
  svkg assess-batch dir/       assess every *.json in a directory
  svkg mcp                     run the read-only Claude Desktop MCP server (stdio)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _print(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="svkg", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch-legislation")
    f.add_argument("--offline", action="store_true", help="re-parse the cached HTML without downloading")
    sub.add_parser("extract")
    sub.add_parser("check")
    sub.add_parser("load")
    sub.add_parser("build")
    a = sub.add_parser("assess")
    a.add_argument("path")
    a.add_argument("--no-write", action="store_true", help="do not write assessment nodes")
    a.add_argument("--summary", action="store_true", help="print only the summary")
    b = sub.add_parser("assess-batch")
    b.add_argument("dir")
    b.add_argument("--no-write", action="store_true")
    sub.add_parser("mcp")
    args = p.parse_args(argv)

    if args.cmd == "fetch-legislation":
        from . import extract, legislation
        if not (Path(__file__).resolve().parent.parent / "regulatory/provisions.json").exists():
            extract.run()
        res = legislation.fetch(download=not args.offline)
        extract.run()
        _print(res)
        print("Next: svkg check && svkg load")
    if args.cmd in ("extract", "build"):
        from . import extract
        _print(extract.run())
    if args.cmd in ("check", "build"):
        from . import check
        rc = check.main()
        if rc:
            return rc
    if args.cmd in ("load", "build"):
        from . import graph
        _print(graph.load())
    if args.cmd == "assess":
        from .service import assess
        res = assess(json.loads(Path(args.path).read_text()), write=not args.no_write)
        _print({"assessment_id": res["assessment_id"], **res["summary"]} if args.summary else res)
    if args.cmd == "assess-batch":
        from .service import assess
        rows = []
        for f in sorted(Path(args.dir).glob("*.json")):
            res = assess(json.loads(f.read_text()), write=not args.no_write, channel="cli-batch")
            rows.append({"file": f.name, "assessment_id": res["assessment_id"], **res["summary"]})
        _print(rows)
    if args.cmd == "mcp":
        from .mcp_server import main as mcp_main
        mcp_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
