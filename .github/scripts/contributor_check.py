#!/usr/bin/env python3
"""
Contributor history check for hwidx pull requests.

Compares the authors of the commits in a PR with the existing history of each
organisation/device tree the PR touches, and writes an informational markdown
report. Only git data is read; nothing from the PR is executed.

Usage: contributor_check.py BASE_SHA HEAD_SHA REPORT_PATH

Env:
  REPO_URL        e.g. https://github.com/owner/repo (used for links)
  GITHUB_OUTPUT   if set, `flag=true|false` is appended to it
"""

import functools
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field

from hwidx_common import code, git, md, write_output

MAX_REPORT_CHARS = 60000
MAX_HISTORY_ROWS = 25

ORG_RE = re.compile(r"^\d+(\.\d+)*$")
NOREPLY_RE = re.compile(r"^(?:\d+\+)?([^@]+)@users\.noreply\.github\.com$", re.I)
REVERT_RE = re.compile(r"This reverts commit ([0-9a-f]{7,40})")
LOG_FORMAT = "%x1e%H%x1f%an%x1f%ae%x1f%aI%x1f%s%x1f%b%x1f"


def identity_keys(name, email):
    """Strong keys (email / GitHub login) and weak keys (display name)."""
    strong, weak = set(), set()
    email = email.strip().lower()
    if email:
        strong.add("email:" + email)
        m = NOREPLY_RE.match(email)
        if m:
            strong.add("gh:" + m.group(1))
    name = " ".join(name.split()).lower()
    if name:
        weak.add("name:" + name)
    return frozenset(strong), frozenset(weak)


def match_strength(a, b):
    """'strong' if email/login match, 'name' if only the display name matches."""
    if a.strong & b.strong:
        return "strong"
    if a.weak & b.weak:
        return "name"
    return None


def tree_of(path):
    """Map a file path to its (org, device) tree, or None if not an index entry."""
    parts = path.split("/")
    if len(parts) < 2 or any(p.startswith(".") for p in parts):
        return None
    if not ORG_RE.match(parts[0]):
        return None
    device = parts[1]
    if len(parts) == 2:
        device = re.sub(r"\.ya?ml$", "", device)
    return (parts[0], device)


def tree_label(tree):
    org, device = tree
    return f"{org}/{device}"


@dataclass(eq=False)
class Commit:
    sha: str
    name: str
    email: str
    date: str
    subject: str
    body: str
    files: list = field(default_factory=list)

    def __post_init__(self):
        self.strong, self.weak = identity_keys(self.name, self.email)

    @property
    def short(self):
        return self.sha[:7]

    @property
    def who(self):
        return f"{self.name} <{self.email}>"

    @property
    def trees(self):
        return {t for t in map(tree_of, self.files) if t}


def read_log(*revs):
    out = git("log", "--no-merges", "--no-renames", "--name-only",
              f"--format={LOG_FORMAT}", *revs)
    commits = []
    for record in out.split("\x1e")[1:]:
        sha, name, email, date, subject, body, files = record.split("\x1f", 6)
        commits.append(Commit(sha, name, email, date[:10], subject, body,
                              [f for f in files.splitlines() if f.strip()]))
    return commits


class Report:
    def __init__(self, repo_url, base, head):
        self.repo_url = repo_url.rstrip("/")
        self.base = base
        self.head = head

    def commit(self, c):
        if not self.repo_url:
            return f"`{c.short}`"
        return f"[`{c.short}`]({self.repo_url}/commit/{c.sha})"

    def path(self, path, ref):
        if not self.repo_url:
            return code(path)
        return f"[{md(path)}]({self.repo_url}/blob/{ref}/{path})"


class Person:
    """A contributor, grouped from one or more commits sharing an email."""

    def __init__(self, commit):
        self.name = commit.name
        self.email = commit.email
        self.strong, self.weak = commit.strong, commit.weak

    @property
    def who(self):
        return f"{self.name} <{self.email}>"


def group_people(commits):
    people = {}
    for c in commits:
        people.setdefault(c.email.strip().lower(), Person(c))
    return list(people.values())


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    base, head, report_path = sys.argv[1:]
    base, head = (git("rev-parse", f"{rev}^{{commit}}").strip() for rev in (base, head))
    rpt = Report(os.environ.get("REPO_URL", ""), base, head)

    history = read_log(base)                    # newest first
    pr_commits = read_log(f"{base}..{head}")[::-1]  # oldest first
    order = {c.sha: i for i, c in enumerate(history)}
    by_sha = {c.sha: c for c in history}
    base_files = set(git("ls-tree", "-r", "--name-only", base).splitlines())

    reverted_by = {}
    for c in history:
        for sha in REVERT_RE.findall(c.body):
            for full in by_sha:
                if full.startswith(sha):
                    reverted_by.setdefault(full, c)

    tree_history = defaultdict(list)
    org_history = defaultdict(list)
    tree_files = defaultdict(set)
    for c in history:
        for f in c.files:
            t = tree_of(f)
            if t:
                tree_history[t].append(c)
                org_history[t[0]].append(c)
                if f in base_files:
                    tree_files[t].add(f)
    for t in tree_history:
        tree_history[t] = list(dict.fromkeys(tree_history[t]))
    for o in org_history:
        org_history[o] = list(dict.fromkeys(org_history[o]))

    @functools.cache
    def blame(path):
        """Commit sha for each line of `path` as it stands on the base branch."""
        if path not in base_files:
            return ()
        out = git("blame", "--line-porcelain", base, "--", path)
        return tuple(l.split(" ", 1)[0] for l in out.splitlines()
                     if re.match(r"^[0-9a-f]{40} ", l))

    @functools.cache
    def added_lines(sha):
        out = git("show", "--numstat", "-M", "--format=", sha)
        total = 0
        for line in out.splitlines():
            added = line.split("\t", 1)[0]
            total += int(added) if added.isdigit() else 0
        return total

    def surviving_lines(commit):
        return sum(blame(f).count(commit.sha) for f in commit.files)

    def lines_by_person(files, person):
        n = 0
        for f in files:
            for sha in blame(f):
                c = by_sha.get(sha)
                if c and match_strength(c, person) == "strong":
                    n += 1
        return n

    def later_edits(commit, person):
        """Later commits by someone else that touched the same files."""
        idx = order[commit.sha]
        files = set(commit.files)
        return [c for c in history[:idx]
                if files & set(c.files) and match_strength(c, person) is None]

    def contributors_table(commits, files):
        rows = defaultdict(list)
        people = group_people(commits)
        for c in commits:
            rows[c.email.strip().lower()].append(c)
        out = ["| Contributor | Commits | First | Most recent | Lines in current version |",
               "|---|---|---|---|---|"]
        for p in people:
            cs = rows[p.email.strip().lower()]
            out.append(f"| {md(p.who)} | {len(cs)} | {cs[-1].date} {rpt.commit(cs[-1])} "
                       f"| {cs[0].date} {rpt.commit(cs[0])} | {lines_by_person(files, p)} |")
        return out

    # Net changes in this PR, grouped by tree.
    pr_changes = defaultdict(list)
    for line in git("diff", "--name-status", "--no-renames", f"{base}...{head}").splitlines():
        status, path = line.split("\t", 1)
        t = tree_of(path)
        if t:
            pr_changes[t].append((status[0], path))

    pr_people = group_people(pr_commits)
    pr_trees = defaultdict(list)  # tree -> PR authors who touched it
    for c in pr_commits:
        for t in sorted(c.trees):
            if not any(match_strength(c, p) == "strong" for p in pr_trees[t]):
                pr_trees[t].append(Person(c))
    for t in pr_changes:
        pr_trees.setdefault(t, [])

    verbs = {"A": "added", "M": "modified", "D": "deleted"}
    flags = []
    sections = []

    for t in sorted(pr_trees, key=lambda t: tree_label(t)):
        org = t[0]
        changes = pr_changes.get(t, [])
        lines = [f"### {code(tree_label(t))}", ""]
        if changes:
            lines.append("Changes in this PR: " + ", ".join(
                f"{verbs.get(s, s)} {rpt.path(p, base if s == 'D' else head)}"
                for s, p in changes))
            lines.append("")
        modifies_existing = any(s in "MD" for s, _ in changes)

        if org not in org_history:
            flags.append(t)
            lines += [
                f"🔍 **New organisation.** There are no earlier commits under {code(org + '/')}, "
                "so there is no history to compare the author against.",
                "",
                "_Suggested check:_ confirm the organisation prefix is the one intended for this "
                "submitter, and that the organisation details (e.g. an `index.yml`) look right.",
            ]
        elif t not in tree_history:
            flags.append(t)
            known_in_org = [p for p in pr_trees[t]
                            if any(match_strength(c, p) for c in org_history[org])]
            lines += [
                f"🔍 **New device** in the existing organisation {code(org)}.",
                "",
            ]
            if known_in_org:
                lines += [", ".join(md(p.who) for p in known_in_org)
                          + f" has earlier commits elsewhere in {code(org + '/')} (see below).", ""]
            else:
                lines += [f"None of this PR's commit authors appear in the earlier history of {code(org + '/')}.", ""]
            lines += [
                f"<details><summary>Earlier contributors to <code>{md(org)}/</code></summary>",
                "",
                *contributors_table(org_history[org][:MAX_HISTORY_ROWS * 4],
                                    [f for tt, fs in tree_files.items() if tt[0] == org for f in fs]),
                "", "</details>", "",
                "_Suggested check:_ confirm the submitter is associated with this organisation "
                f"(for example via {code(org + '/index.yml')}, or with one of its earlier contributors), "
                "and that the device number does not clash with one planned elsewhere.",
            ]
        else:
            for p in pr_trees[t] or [None]:
                if p is None:
                    break
                matches = [c for c in tree_history[t] if match_strength(c, p)]
                strong = [c for c in matches if match_strength(c, p) == "strong"]
                if strong:
                    last = strong[0]
                    lines.append(
                        f"✅ {md(p.who)} has {len(strong)} earlier commit(s) to this device, "
                        f"most recently {rpt.commit(last)} on {last.date}.")
                elif matches:
                    flags.append(t)
                    last = matches[0]
                    lines.append(
                        f"🔍 {md(p.who)} shares a name with an earlier contributor to this device "
                        f"({md(last.who)}, {rpt.commit(last)} on {last.date}) but uses a different email. "
                        "This is common when someone works from a different machine or account.")
                    lines.append("")
                    lines.append("_Suggested check:_ a quick confirmation that this is the same person is probably enough.")
                else:
                    flags.append(t)
                    lines.append(
                        f"🔍 This is the first contribution from {md(p.who)} to this device.")
                    if modifies_existing:
                        lines.append("")
                        lines.append("_Suggested check:_ this PR changes or removes existing entries, so it may be "
                                     "worth checking the edits with the earlier contributors listed below.")
                    else:
                        lines.append("")
                        lines.append("_Suggested check:_ the PR only adds new files, so confirm the new entry "
                                     "is consistent with the existing ones and that the submitter is associated with this device.")
                lines.append("")
            lines += [
                "<details><summary>Earlier contributors to this device</summary>",
                "",
                *contributors_table(tree_history[t], sorted(tree_files[t])),
                "", "</details>",
            ]
        sections.append("\n".join(lines))

    author_sections = []
    for p in pr_people:
        own = [c for c in history if match_strength(c, p)]
        lines = [f"### {md(p.who)}", ""]
        pr_own = [c for c in pr_commits if match_strength(c, p) == "strong"]
        lines.append("Commits in this PR: " + ", ".join(
            f"{rpt.commit(c)} ({c.date})" for c in pr_own))
        lines.append("")
        if not own:
            lines.append("No earlier commits by this author were found in the repository history. "
                         "This is expected for a first contribution.")
            author_sections.append("\n".join(lines))
            continue
        name_only = [c for c in own if match_strength(c, p) == "name"]
        lines.append(f"{len(own)} earlier commit(s) found"
                     + (f" ({len(name_only)} matched by name only, with a different email)" if name_only else "")
                     + ". Their current state on the base branch:")
        lines += ["", "| Commit | Date | Where | Subject | In current version |", "|---|---|---|---|---|"]
        for c in own[:MAX_HISTORY_ROWS]:
            where = ", ".join(code(tree_label(t)) for t in sorted(c.trees)) or "_(repository files)_"
            added = added_lines(c.sha)
            kept = surviving_lines(c)
            if c.sha in reverted_by:
                r = reverted_by[c.sha]
                state = f"Reverted in {rpt.commit(r)} ({r.date})"
            elif not any(f in base_files for f in c.files):
                state = "File(s) since removed"
            elif added == 0:
                state = "No new lines (renames or removals only)"
            elif kept >= added:
                state = f"All {added} line(s) present"
            else:
                state = f"{kept} of {added} line(s) present"
            if c.sha not in reverted_by and kept < added:
                edits = later_edits(c, p)
                if edits:
                    state += "; later edited in " + ", ".join(
                        f"{rpt.commit(e)} by {md(e.name)} ({e.date})" for e in edits[:3])
                    if len(edits) > 3:
                        state += f" and {len(edits) - 3} more"
            lines.append(f"| {rpt.commit(c)} | {c.date} | {where} | {md(c.subject[:72])} | {state} |")
        if len(own) > MAX_HISTORY_ROWS:
            lines.append(f"\n_…and {len(own) - MAX_HISTORY_ROWS} older commit(s) not shown._")
        author_sections.append("\n".join(lines))

    unique_flags = list(dict.fromkeys(flags))
    out = [
        "<!-- contributorbot-report -->",
        "## Contributor history check",
        "",
        "This is an automated, informational summary of how the authors of this PR's commits relate to "
        "the existing history of the parts of the index it touches. It is not a judgement on the "
        "contribution, just context to help a reviewer decide where to look. Git author names and emails "
        "are self-reported, so a match is a helpful hint rather than proof of identity.",
        "",
    ]
    if not pr_trees:
        out.append("ℹ️ This PR does not touch any organisation/device entries, so there was nothing to compare.")
    elif unique_flags:
        out.append(f"**🔍 {len(unique_flags)} location(s) may merit a closer look:** "
                   + ", ".join(code(tree_label(t)) for t in unique_flags))
    else:
        out.append("**✅ Every commit author has contributed to the touched device(s) before.**")
    out += ["", "## Where this PR makes changes", ""]
    out.append("\n\n".join(sections) if sections else "_No index entries changed._")
    out += ["", "## Earlier contributions from this PR's authors", ""]
    out.append("\n\n".join(author_sections) if author_sections else "_No commits found in this PR._")

    report = "\n".join(out) + "\n"
    if len(report) > MAX_REPORT_CHARS:
        report = report[:MAX_REPORT_CHARS] + "\n\n_…report truncated; see the workflow run for the full output._\n"
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(report)

    write_output("flag", "true" if unique_flags else "false")


if __name__ == "__main__":
    main()
