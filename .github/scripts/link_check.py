#!/usr/bin/env python3
"""
File-type, YAML and URL checks for hwidx pull requests.

For each file the PR adds or changes (dotfiles/dot-directories and markdown are
ignored):
  - only .yml / .yaml files are allowed
  - the file must be valid YAML, a mapping at the top level, without duplicate keys
  - the `url` field, if present, must be an http(s) link returning 200 after redirects
  - any other links found in the file are checked too, but only reported as warnings

Only git data is read; nothing from the PR is executed.

Usage: link_check.py BASE_SHA HEAD_SHA REPORT_PATH

Env:
  REPO_URL        e.g. https://github.com/owner/repo (used for links)
  RUN_URL         link to the workflow run, shown at the top of the report
  GITHUB_OUTPUT   if set, `result=pass|fail` is appended to it
"""

import os
import re
import socket
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import yaml

from hwidx_common import code, git, md, write_output

MAX_REPORT_CHARS = 60000
TIMEOUT = 20
WORKERS = 8
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0 "
              "hwidx-linkbot (+https://hwidx.org/)")

URL_RE = re.compile(r"https?://[^\s<>\"'`]+")
TRAILING_PUNCT = ".,;:!?)]}'\""


class UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys (e.g. two `url:` lines)."""

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping", node.start_mark,
                    f"found duplicate key {key!r}", key_node.start_mark)
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def is_ignored(path):
    parts = path.split("/")
    return any(p.startswith(".") for p in parts) or path.endswith(".md")


def changed_files(base, head):
    """Paths added, modified, renamed or copied by the PR (as they are at head)."""
    out = git("diff", "--name-status", "-z", f"{base}...{head}").split("\0")
    paths, i = [], 0
    while i < len(out) - 1:
        status = out[i]
        if status[0] in "RC":
            paths.append(out[i + 2])
            i += 3
        else:
            if status[0] in "AM":
                paths.append(out[i + 1])
            i += 2
    return paths


def find_links(value, where=""):
    """Yield (field path, url) for every link in any string value."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from find_links(v, f"{where}.{k}" if where else str(k))
    elif isinstance(value, list):
        for n, v in enumerate(value):
            yield from find_links(v, f"{where}[{n}]")
    elif isinstance(value, str):
        for url in URL_RE.findall(value):
            yield where, url.rstrip(TRAILING_PUNCT)


def check_url(url):
    """Return (ok, description) after following redirects, retrying once."""
    result = (False, "not checked")
    for _ in range(2):
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                resp.read(1024)
                code = resp.status
                return (code == 200, f"HTTP {code}")
        except urllib.error.HTTPError as err:
            result = (False, f"HTTP {err.code}")
            if err.code < 500 and err.code != 429:
                return result
        except (TimeoutError, socket.timeout):
            result = (False, f"timed out after {TIMEOUT}s")
        except urllib.error.URLError as err:
            reason = err.reason
            if isinstance(reason, ssl.SSLError):
                return (False, f"TLS error: {reason.reason or reason}")
            if isinstance(reason, socket.gaierror):
                return (False, "host not found")
            if isinstance(reason, (TimeoutError, socket.timeout)):
                result = (False, f"timed out after {TIMEOUT}s")
            else:
                result = (False, f"connection failed: {reason}")
        except (ValueError, OSError) as err:
            return (False, f"invalid URL: {err}")
    return result


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    base, head, report_path = sys.argv[1:]
    base, head = (git("rev-parse", f"{rev}^{{commit}}").strip() for rev in (base, head))
    repo_url = os.environ.get("REPO_URL", "").rstrip("/")

    def file_link(path):
        if not repo_url:
            return code(path)
        return f"[{md(path)}]({repo_url}/blob/{head}/{urllib.request.quote(path)})"

    files = [p for p in changed_files(base, head) if not is_ignored(p)]
    bad_types = [p for p in files if not re.search(r"\.ya?ml$", p)]
    yaml_files = [p for p in files if p not in bad_types]

    yaml_errors = []      # (path, markdown message)
    no_url = []           # paths without a `url` field
    primary = []          # (path, url)
    secondary = []        # (path, field, url)
    for path in yaml_files:
        try:
            text = subprocess.run(["git", "show", f"{head}:{path}"],
                                  check=True, capture_output=True).stdout.decode("utf-8")
        except UnicodeDecodeError:
            yaml_errors.append((path, "file is not valid UTF-8"))
            continue
        try:
            data = yaml.load(text, Loader=UniqueKeyLoader)
        except yaml.YAMLError as err:
            mark = getattr(err, "problem_mark", None)
            where = f"line {mark.line + 1}, column {mark.column + 1}: " if mark else ""
            problem = getattr(err, "problem", None) or str(err)
            yaml_errors.append((path, md(f"{where}{problem}")))
            continue
        if not isinstance(data, dict):
            yaml_errors.append((path, "the top level should be a set of `key: value` fields"))
            continue

        url = data.get("url")
        if url is None:
            no_url.append(path)
        elif not isinstance(url, str) or not re.match(r"^https?://\S+$", url.strip()):
            yaml_errors.append((path, "`url` should be a single http:// or https:// link"))
        else:
            primary.append((path, url.strip()))
        for field, link in find_links(data):
            if field != "url":
                secondary.append((path, field, link))

    unique_urls = sorted({u for _, u in primary} | {u for _, _, u in secondary})
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = dict(zip(unique_urls, pool.map(check_url, unique_urls)))

    failed_primary = [(p, u) for p, u in primary if not results[u][0]]
    failed_secondary = [(p, f, u) for p, f, u in secondary if not results[u][0]]

    out = ["<!-- linkbot-report -->", "## LinkBot PR checks", ""]
    if os.environ.get("RUN_URL"):
        out += [f"- Workflow run: [View run]({os.environ['RUN_URL']})", ""]

    if bad_types:
        out += ["### ❌ File type check failed", "",
                "Only `.yml` or `.yaml` files can be added or changed (markdown and dotfiles are ignored). "
                "These files are not YAML:", ""]
        out += [f"- {file_link(p)}" for p in bad_types]
    else:
        out += ["### ✅ File type check passed", "",
                "All added/changed files are YAML (dotfiles/directories and markdown are ignored)."]
    out.append("")

    if yaml_errors:
        out += ["### ❌ YAML check failed", "", "| File | Problem |", "|---|---|"]
        out += [f"| {file_link(p)} | {msg} |" for p, msg in yaml_errors]
    elif yaml_files:
        out += ["### ✅ YAML check passed", "", f"All {len(yaml_files)} YAML file(s) parsed cleanly."]
    else:
        out += ["### ✅ YAML check skipped", "", "No YAML files were added or changed."]
    if no_url:
        out += ["", "ℹ️ No `url` field (fine for reserved/pending entries and organisation `index.yml` files): "
                + ", ".join(file_link(p) for p in no_url)]
    out.append("")

    if not unique_urls:
        out += ["### ✅ URL check skipped", "", "No URLs were found in added/changed YAML files."]
    elif failed_primary:
        out += ["### ❌ URL check failed", "",
                "The `url` field of these entries did not return `200` after redirects:", "",
                "| File | URL | Result |", "|---|---|---|"]
        out += [f"| {file_link(p)} | {md(u)} | {md(results[u][1])} |" for p, u in failed_primary]
    else:
        out += ["### ✅ URL check passed", "",
                f"Checked {len(primary)} `url` field(s) and {len(secondary)} other link(s); "
                "every `url` field returned **200** after redirects."]
    if failed_secondary:
        out += ["", "<details><summary>⚠️ "
                f"{len(failed_secondary)} other link(s) did not return 200 (informational, does not fail the check)"
                "</summary>", "",
                "| File | Field | URL | Result |", "|---|---|---|---|"]
        out += [f"| {file_link(p)} | {code(f)} | {md(u)} | {md(results[u][1])} |"
                for p, f, u in failed_secondary]
        out += ["", "</details>"]

    report = "\n".join(out) + "\n"
    if len(report) > MAX_REPORT_CHARS:
        report = report[:MAX_REPORT_CHARS] + "\n\n_…report truncated; see the workflow run for the full output._\n"
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(report)

    failed = bool(bad_types or yaml_errors or failed_primary)
    write_output("result", "fail" if failed else "pass")


if __name__ == "__main__":
    main()
