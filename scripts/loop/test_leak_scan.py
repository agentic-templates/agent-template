"""Tests scripts/leak-scan, scripts/git-hooks/pre-push and scripts/install-git-hooks with planted leaks in repositories
that the tests make. The tests join each secret, socket path and path into a home folder from two parts, so that this
file holds none of them."""

import contextlib
import importlib.machinery
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NamedTuple
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent
LEAK_SCAN = SCRIPTS / "leak-scan"
# The scan's file name has no .py, so the tests load it by its path.
loader = importlib.machinery.SourceFileLoader("leak_scan", str(LEAK_SCAN))
leak_scan = types.ModuleType(loader.name)
leak_scan.__file__ = loader.path
loader.exec_module(leak_scan)

ZEROS = "0" * 40
KEY = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCd"
TOKENS = [
    "gh" + "p_" + KEY,
    "gh" + "o_" + KEY,
    "gh" + "u_" + KEY,
    "gh" + "s_" + KEY,
    "gh" + "r_" + KEY,
    "github" + "_pat_11ABCDEFG0_" + KEY,
    "glp" + "at-" + KEY[:20],
    "sk-" + "ant-api03-" + KEY,
    "sk-" + "proj-" + KEY,
    "sk-" + KEY,
    "sk_" + "live_" + KEY,
    "rk_" + "live_" + KEY,
    "AKI" + "AIOSFODNN7EXAMPLE",
    "ASI" + "AIOSFODNN7EXAMPLE",
    "xox" + "b-1234567890-abcdefghij",
    "xox" + "p-1234567890-abcdefghij",
    "AIz" + "aSyA-" + KEY[:31],
]
TOKEN = TOKENS[0]
AWS_VALUE = "wJalrXUtnFEMI/K7" + "MDENG/bPxRfiCYEXAMPLEKEY"
BEGIN = "-----BEGIN "
PRIVATE_LINE = "The reference design keeps its quarterly numbers in the vault."
ELSEWHERE_LINE = "A file elsewhere, which a link in a private folder points to, holds this sentence."
CODE_LINES = [
    "for line in text.splitlines():",
    "    parts = line.split(maxsplit=2)",
    "    totals[parts[0]] += int(parts[1])",
]
SMALL_FILE = "region = north\nlimit = 42\n"
LICENSE = "MIT License\nCopyright the authors\n"
GIT_CONFIG = "[core]\n\tbare = false, which is long enough to count as a line\n"
USERS = "/Users"
HOMES = "/home"
TMUX_FOLDER = "tmux-" + "501"

Files = Mapping[str, str | bytes]


class Result(NamedTuple):
    status: int
    output: str


class Fixture(NamedTuple):
    """The folders that the tests share: a home folder, private folders, and a repository with its remote."""

    base: Path
    home: Path
    inbox: Path
    private: Path
    repo: Path
    remote: Path
    start: str
    # The home folder that the tests run in, where uv keeps the Python that runs the hook
    real_home: str


fixture: Fixture


def git(*arguments: str, stdin: str | None = None) -> str:
    done = subprocess.run(["git", *arguments], input=stdin, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)


def setUpModule() -> None:
    global fixture
    unittest.addModuleCleanup(os.chdir, os.getcwd())
    real_home = os.environ.get("HOME", "")
    base = Path(tempfile.mkdtemp()).resolve()
    unittest.addModuleCleanup(shutil.rmtree, base, ignore_errors=True)
    home = base / "home"
    environment = {
        # git looks for no repository above the temporary folder, and reads none of the machine's settings.
        "GIT_CEILING_DIRECTORIES": str(base),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Tester",
        "GIT_AUTHOR_EMAIL": "tester@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "Tester",
        "GIT_COMMITTER_EMAIL": "tester@users.noreply.github.com",
        # A stand-in home folder, user and terminal sockets, and a loop folder with the list of private folders
        "HOME": str(home),
        "USER": "leaktester",
        "LOGNAME": "leaktester",
        "KITTY_LISTEN_ON": f"unix:{base}/sockets/kitty-4242",
        "TMUX": f"{base}/sockets/{TMUX_FOLDER}/default,4242,0",
        "LOOP_DIR": str(base / "loop"),
    }
    patch = mock.patch.dict(os.environ, environment)
    patch.start()
    unittest.addModuleCleanup(patch.stop)
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        os.environ.pop(name, None)

    # Two private folders: one in the home folder, listed with ~, and one outside it
    inbox, private = home / "inbox", base / "private"
    write(base / "loop" / "leak-scan.paths", f"# The private folders\n~/inbox\n\n{private}\n")
    write(private / "notes" / "plan.txt", f"short line\n  {PRIVATE_LINE}  \n")
    write(private / "notes" / "code.py", "def total(text):\n" + "\n".join(CODE_LINES) + "\n    return totals\n")
    # A small file whose lines are too short to count alone, a trivial file, and a file that the public history holds
    write(inbox / "settings.ini", SMALL_FILE)
    write(inbox / "count.txt", "1\n")
    write(inbox / "LICENSE", LICENSE)
    write(inbox / "blob.bin", b"binary\0data that is long enough to count as a line here\n")
    # The files that the scan skips: a file that a symbolic link points to, and a git folder
    write(base / "elsewhere.txt", ELSEWHERE_LINE + "\n")
    os.symlink(base / "elsewhere.txt", inbox / "link.txt")
    write(inbox / ".git" / "config", GIT_CONFIG)

    repo, remote = base / "repo", base / "remote.git"
    git("init", "--quiet", "--bare", "--initial-branch=main", str(remote))
    git("init", "--quiet", "--initial-branch=main", str(repo))
    os.chdir(repo)
    git("remote", "add", "origin", str(remote))
    write(repo / "README.md", "# A project\n")
    write(repo / "LICENSE", LICENSE)
    git("add", "README.md", "LICENSE")
    git("commit", "--quiet", "-m", "chore: start the project")
    git("push", "--quiet", "origin", "main")
    fixture = Fixture(base, home, inbox, private, repo, remote, git("rev-parse", "HEAD"), real_home)


class LeakScanTest(unittest.TestCase):
    """A test that works in the shared repository, or in a copy of it."""

    def setUp(self) -> None:
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(fixture.repo)
        self.remote = fixture.remote

    def use_copy(self) -> None:
        """Works in a copy of the repository and its remote, for a test that changes them."""
        folder = Path(tempfile.mkdtemp(dir=fixture.base))
        shutil.copytree(fixture.repo, folder / "repo", symlinks=True)
        shutil.copytree(fixture.remote, folder / "remote.git")
        self.remote = folder / "remote.git"
        os.chdir(folder / "repo")
        git("remote", "set-url", "origin", str(self.remote))

    def plant(self, message: str, files: Files | None = None) -> str:
        """Commits files on a new commit after the pushed start, and returns the commit."""
        git("checkout", "--quiet", "--detach", fixture.start)
        for path, content in (files or {}).items():
            write(Path(path), content)
            git("add", "--", path)
        git("commit", "--quiet", "--allow-empty", "-m", message)
        return git("rev-parse", "HEAD")

    def scan(self, *arguments: str, stdin: str = "") -> Result:
        """Runs the scan in this process, with its output and its errors together."""
        output = io.StringIO()
        with (
            mock.patch.object(sys, "stdin", io.StringIO(stdin)),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(output),
        ):
            try:
                status = leak_scan.main(list(arguments))
            except SystemExit as exit:
                assert isinstance(exit.code, int)
                status = exit.code
        return Result(status, output.getvalue())

    def scan_push(self, commit: str, remote: str = "origin") -> Result:
        """Runs the scan as the pre-push hook of a push of a commit to a new branch."""
        return self.scan(remote, str(self.remote), stdin=f"refs/heads/topic {commit} refs/heads/topic {ZEROS}\n")

    def assert_finding(self, result: Result, kind: str, label: str, place: str, secret: str = "") -> None:
        self.assertEqual(result.status, 1, result.output)
        self.assertIn(f"{kind} in {label} {place}", result.output)
        if secret:
            self.assertNotIn(secret, result.output)

    def assert_clean(self, result: Result) -> None:
        self.assertEqual(result.status, 0, result.output)
        self.assertIn("scripts/leak-scan found nothing in ", result.output)

    def assert_error(self, result: Result, message: str) -> None:
        self.assertEqual(result.status, 2, result.output)
        self.assertIn(message, result.output)

    def scan_planted(self, cases: Sequence[Files]) -> tuple[list[str], Result]:
        """Commits the files of each case on a commit of its own, and scans a push of each commit to a branch of its
        own in one run, because a run costs more than the rest of a case. Each finding names its commit."""
        commits = [self.plant("feat: add a file", files) for files in cases]
        lines = [
            f"refs/heads/case-{number} {commit} refs/heads/case-{number} {ZEROS}\n"
            for number, commit in enumerate(commits)
        ]
        return commits, self.scan("origin", str(self.remote), stdin="".join(lines))

    def assert_planted_findings(self, kind: str, cases: Sequence[tuple[str, str, Files]]) -> None:
        """Checks that the scan finds a kind of leak at the place of each case, without showing the case's secret:
        each case is a place, a secret and the files."""
        commits, result = self.scan_planted([files for _, _, files in cases])
        for commit, (place, secret, _) in zip(commits, cases, strict=True):
            with self.subTest(place=place, secret=secret[:6]):
                self.assert_finding(result, kind, commit[:12], place, secret)

    def assert_planted_finding(self, kind: str, place: str, secret: str, files: Files) -> None:
        self.assert_planted_findings(kind, [(place, secret, files)])

    def assert_planted_clean(self, *cases: Files) -> None:
        self.assert_clean(self.scan_planted(cases)[1])


class PrivateFolderTest(LeakScanTest):
    def test_finds_a_sentence_from_a_private_file(self) -> None:
        self.assert_planted_finding(
            "private-line", "notes.md:2", PRIVATE_LINE, {"notes.md": f"Intro\n{PRIVATE_LINE}\n"}
        )

    def test_finds_a_sentence_from_a_private_file_with_other_white_space_and_wrappers(self) -> None:
        spaced = "  The reference   design keeps its quarterly numbers in the vault.  "
        cases: list[tuple[str, str, Files]] = [
            ("notes.md:1", "quarterly", {"notes.md": spaced}),
            ("quote.md:1", "quarterly", {"quote.md": f"> {PRIVATE_LINE}"}),
            ("notes.py:1", "quarterly", {"notes.py": f"# {PRIVATE_LINE}"}),
            ("notes.json:2", "quarterly", {"notes.json": f'[\n  "{PRIVATE_LINE}",\n]'}),
            ("list.md:1", "quarterly", {"list.md": f"- {PRIVATE_LINE}"}),
        ]
        self.assert_planted_findings("private-line", cases)

    def test_finds_a_sentence_from_a_private_file_in_a_commit_message(self) -> None:
        commit = self.plant(f"docs: add the plan\n\n{PRIVATE_LINE}")
        self.assert_finding(self.scan_push(commit), "private-line", commit[:12], "message line 3", "quarterly")

    def test_finds_each_copy_of_a_small_private_file(self) -> None:
        commit = self.plant(
            "feat: add settings", {"config/settings.ini": SMALL_FILE, "backup/settings.ini": SMALL_FILE}
        )
        result = self.scan_push(commit)
        for place in ("config/settings.ini", "backup/settings.ini"):
            with self.subTest(place=place):
                self.assert_finding(result, "private-file", commit[:12], place, "north")

    def test_finds_a_copy_of_a_large_private_file_but_not_its_lines(self) -> None:
        # The other tests would read the large file in each scan, so this one lists a folder of its own.
        loop = Path(tempfile.mkdtemp(dir=fixture.base))
        line = "An archived line that is long enough to count, and it reads as prose."
        # The NUL bytes make the file binary, so that the scan of its copy reads only its first line.
        archive = (line + "\n").encode() + b"\0" * leak_scan.MAX_FINGERPRINT_BYTES
        write(loop / "archive" / "archive.bin", archive)
        write(loop / "leak-scan.paths", f"{loop}/archive\n")
        with mock.patch.dict(os.environ, {"LOOP_DIR": str(loop)}):
            self.assert_planted_finding("private-file", "archive.bin", "", {"archive.bin": archive})
            self.assert_planted_clean({"notes.md": line + "\n"})

    def test_finds_the_path_of_a_private_folder(self) -> None:
        cases: list[tuple[str, str, Files]] = [
            ("notes.md:1", "", {"notes.md": f"Copied from {fixture.private}/notes.\n"}),
            ("notes.md:1", "", {"notes.md": "Copied from ~/inbox/letter.txt\n"}),
        ]
        self.assert_planted_findings("private-folder", cases)

    def test_counts_the_path_of_a_private_folder_in_the_home_folder_as_a_private_folder(self) -> None:
        commit = self.plant("feat: add a file", {"notes.md": f"Copied from {fixture.inbox}/letter.txt\n"})
        result = self.scan_push(commit)
        self.assert_finding(result, "private-folder", commit[:12], "notes.md:1", str(fixture.inbox))
        self.assertNotIn("home-path", result.output)

    def test_finds_a_block_of_3_lines_of_code_in_a_row(self) -> None:
        content = "def add(text):\n" + "\n".join(CODE_LINES) + "\n"
        self.assert_planted_finding("private-line", "src/totals.py:2-4", "maxsplit", {"src/totals.py": content})

    def test_leaves_out_lines_of_code_that_are_fewer_than_3_in_a_row(self) -> None:
        self.assert_planted_clean(
            {"src/totals.py": f"def add(text):\n{CODE_LINES[0]}\n    print(line)\n"},
            {"src/totals.py": f"{CODE_LINES[0]}\n{CODE_LINES[1]}\n    print(parts)\n"},
            {"src/totals.py": f"{CODE_LINES[0]}\n{CODE_LINES[1]}\n", "src/more.py": CODE_LINES[2] + "\n"},
        )

    def test_leaves_out_trivial_and_public_files_and_the_files_that_it_skips(self) -> None:
        self.assert_planted_clean(
            {
                "count.txt": "1\n",
                "docs/LICENSE": LICENSE,
                "notes.md": f"short line\n{ELSEWHERE_LINE}\n",
                "config": GIT_CONFIG,
            }
        )

    def test_stops_on_a_listed_folder_that_is_missing(self) -> None:
        write(fixture.base / "missing" / "leak-scan.paths", f"{fixture.base}/no-such-folder\n")
        with mock.patch.dict(os.environ, {"LOOP_DIR": str(fixture.base / "missing")}):
            result = self.scan_push(self.plant("feat: add notes", {"notes.md": "Intro\n"}))
        self.assert_error(result, "the private folder on line 1 of ")
        self.assertIn("isn't a folder that exists. Fix the line, or remove it.", result.output)
        self.assertNotIn("no-such-folder", result.output)

    def test_stops_on_a_list_or_a_private_file_or_folder_that_it_cant_read(self) -> None:
        loop = Path(tempfile.mkdtemp(dir=fixture.base))
        copy = loop / "private"
        shutil.copytree(fixture.private, copy)
        write(loop / "leak-scan.paths", f"{copy}\n")
        commit = self.plant("feat: add notes", {"notes.md": "Intro\n"})
        cant_read = "can't read a file or folder in the private folder on line 1"
        with mock.patch.dict(os.environ, {"LOOP_DIR": str(loop)}):
            for path, message in ((copy / "notes" / "code.py", cant_read), (copy / "notes", cant_read)):
                with self.subTest(path=path.name):
                    path.chmod(0)
                    self.addCleanup(path.chmod, 0o755)
                    self.assert_error(self.scan_push(commit), message)
                    path.chmod(0o755)
            (loop / "leak-scan.paths").chmod(0)
            self.addCleanup((loop / "leak-scan.paths").chmod, 0o644)
            self.assert_error(self.scan_push(commit), f"can't read {loop}/leak-scan.paths: Permission denied")

    def test_scans_without_a_list(self) -> None:
        with mock.patch.dict(os.environ, {"LOOP_DIR": str(fixture.base / "no-loop")}):
            self.assert_planted_clean({"notes.md": PRIVATE_LINE + "\n"})


class HomePathTest(LeakScanTest):
    def test_finds_the_home_folder_in_a_commit_message(self) -> None:
        commit = self.plant(f"fix: read the cache from {fixture.home}/cache")
        self.assert_finding(self.scan_push(commit), "home-path", commit[:12], "message line 1", str(fixture.home))

    def test_finds_each_form_of_the_home_folder(self) -> None:
        home = str(fixture.home)
        slug = home.replace("/", "-").replace(".", "-")
        json_home, unicode_home = home.replace("/", "\\/"), home.replace("/", "\\u002f")
        cases: list[tuple[str, str, Files]] = [
            ("config.txt:1", home, {"config.txt": f"cache = {home}/cache"}),
            ("path copy$HOME/notes.txt", home, {f"copy{home}/notes.txt": "notes"}),
            ("config.json:1", json_home, {"config.json": f'{{"cache": "{json_home}\\/cache"}}'}),
            ("config.json:1", unicode_home, {"config.json": f'{{"cache": "{unicode_home}\\u002fcache"}}'}),
            ("config.txt:1", slug, {"config.txt": f"projects = ~/.claude/projects/{slug}-app"}),
            ("config.txt:1", slug, {"config.txt": f"project = {slug}"}),
            ("config.txt:1", "/leaktester/", {"config.txt": f"cache = {USERS}/leaktester/cache"}),
            ("config.txt:1", "/leaktester/", {"config.txt": f"cache = {HOMES}/leaktester/cache"}),
        ]
        self.assert_planted_findings("home-path", cases)

    def test_finds_the_home_folder_in_the_authors_name(self) -> None:
        with mock.patch.dict(os.environ, {"GIT_AUTHOR_NAME": f"Tester of {fixture.home}"}):
            commit = self.plant("feat: work")
        self.assert_finding(self.scan_push(commit), "home-path", commit[:12], "author header", str(fixture.home))

    def test_leaves_out_paths_that_only_start_like_the_home_folder_and_placeholders(self) -> None:
        home = str(fixture.home)
        slug = home.replace("/", "-").replace(".", "-")
        self.assert_planted_clean(
            {
                "config.txt": f"project = {slug}x-app\ncache = {home}-old/cache and {USERS}/leaktesterson/cache\n"
                f"Use {USERS}/<name>/ or {HOMES}/$USER/\n"
            }
        )

    def test_leaves_out_the_home_folder_of_an_account_that_no_person_uses(self) -> None:
        runner_home = f"{HOMES}/runner"
        environment = {
            "HOME": runner_home,
            "USER": "runner",
            "LOGNAME": "runner",
            "LOOP_DIR": str(fixture.base / "no-loop"),
        }
        with mock.patch.dict(os.environ, environment):
            self.assert_planted_clean({"config.txt": f"cache = {runner_home}/work/cache\n"})


class SocketPathTest(LeakScanTest):
    def test_finds_the_socket_paths_of_the_terminal(self) -> None:
        kitty = os.environ["KITTY_LISTEN_ON"].removeprefix("unix:")
        tmux = os.environ["TMUX"].split(",")[0]
        cases: list[tuple[str, str, Files]] = [
            ("config.txt:1", "kitty-4242", {"config.txt": f"listen_on {kitty}"}),
            ("config.txt:1", TMUX_FOLDER, {"config.txt": f"socket {tmux}"}),
        ]
        self.assert_planted_findings("socket-path", cases)

    def test_finds_a_socket_path_in_private_tmp_written_without_private(self) -> None:
        socket = "/tmp/my-" + "terminal-4242"
        with mock.patch.dict(os.environ, {"KITTY_LISTEN_ON": "unix:/private" + socket}):
            self.assert_planted_finding(
                "socket-path", "config.txt:1", "terminal-4242", {"config.txt": f"socket {socket}"}
            )

    def test_finds_socket_paths_by_their_shape_without_the_terminals_environment(self) -> None:
        sockets = [
            "/tmp/kit" + "ty-31337",
            "unix:/tmp/my" + "kitty-31337",
            "/private/tmp/tmux-" + "502/default",
            "/tmp/cc-socks/" + "49123.sock",
            f"{fixture.base}/other/.claude/agent-coord/" + "coord.sock",
        ]
        cases: list[tuple[str, str, Files]] = [
            ("config.txt:1", socket.rsplit("/", 1)[1], {"config.txt": f"socket = {socket}"}) for socket in sockets
        ]
        with mock.patch.dict(os.environ, {"KITTY_LISTEN_ON": "", "TMUX": ""}):
            self.assert_planted_findings("socket-path", cases)

    def test_leaves_out_kitty_and_tmux_in_prose(self) -> None:
        self.assert_planted_clean({"notes.md": "Run the loop in kitty or in tmux, and see /usr/share/kitty/\n"})


class TokenTest(LeakScanTest):
    def test_finds_a_token_in_the_authors_and_the_committers_email(self) -> None:
        emails = {
            "GIT_AUTHOR_EMAIL": f"tester+{TOKENS[0]}@example.com",
            "GIT_COMMITTER_EMAIL": f"tester+{TOKENS[7]}@example.com",
        }
        with mock.patch.dict(os.environ, emails):
            commit = self.plant("feat: work")
        result = self.scan_push(commit)
        self.assert_finding(result, "token", commit[:12], "author header", TOKENS[0])
        self.assert_finding(result, "token", commit[:12], "committer header", TOKENS[7])

    def test_scans_long_lines_in_seconds(self) -> None:
        # Each line has 200,000 characters. A pattern that tried a match from each of its characters to the line's
        # end would take minutes.
        lines = [
            '{"bytecode": "0x' + "ab12" * 50_000 + '"} tmux-',
            "a_" * 100_000 + " secret",
            "a." * 100_000 + " token",
            "a/" * 100_000 + " kitty",
        ]
        commit = self.plant("feat: add compiled contracts", {"contracts.json": "\n".join(lines) + "\n"})
        started = time.monotonic()
        result = self.scan("--range", f"{fixture.start}..{commit}")
        self.assertLess(time.monotonic() - started, 20)
        self.assert_clean(result)

    def test_finds_each_kind_of_token(self) -> None:
        cases: list[tuple[str, str, Files]] = [
            ("settings.py:1", token, {"settings.py": f'KEY = "{token}"\n'}) for token in TOKENS
        ]
        self.assert_planted_findings("token", cases)

    def test_finds_a_long_secret_assigned_to_a_name_that_says_that_its_a_secret(self) -> None:
        assignments = [
            "AWS_SECRET" + "_ACCESS_KEY=" + AWS_VALUE,
            "export DB_PASS" + f'WORD="{AWS_VALUE}"',
            '"api' + f'_key": "{AWS_VALUE}"',
            "client_sec" + f"ret: {AWS_VALUE}",
        ]
        cases: list[tuple[str, str, Files]] = [
            ("settings.env.sample:1", AWS_VALUE, {"settings.env.sample": assignment + "\n"})
            for assignment in assignments
        ]
        self.assert_planted_findings("token", cases)

    def test_leaves_out_names_placeholders_and_token_prefixes_in_prose(self) -> None:
        self.assert_planted_clean(
            {
                "settings.py": 'token = os.environ["GITHUB_TOKEN"]\nGH_TOKEN: ${{ github.token }}\n'
                "PASSWORD=your-password-goes-here\n",
                "docs.md": "Each token expires. Set GITHUB_TOKEN, and keep the sk- prefix and the ghp_ prefix.\n",
            }
        )

    def test_finds_private_key_blocks(self) -> None:
        cases: list[tuple[str, str, Files]] = [
            ("key.txt:1", "", {"key.txt": BEGIN + "OPENSSH PRIVATE KEY-----\n"}),
            ("server.pem:1", "", {"server.pem": BEGIN + "PRIVATE KEY-----\n"}),
            (
                "rsa.txt:1",
                "",
                {"rsa.txt": BEGIN + "RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----\n"},
            ),
        ]
        self.assert_planted_findings("private-key", cases)

    def test_finds_a_token_in_a_file_that_git_counts_as_binary(self) -> None:
        line = f'KEY = "{TOKEN}"'
        commit = self.plant(
            "feat: add binary files",
            {
                ".gitattributes": "*.dat -diff\n",
                "settings.dat": line + "\n",
                "blob.bin": f"header\0{line}\0footer".encode(),
                "wide.txt": line.encode("utf-16-le"),
                "big-endian.txt": line.encode("utf-16-be"),
            },
        )
        result = self.scan_push(commit)
        for place in (
            "settings.dat:1",
            "blob.bin, text 1 in the binary file",
            "wide.txt, text 1 in the binary file",
            "big-endian.txt, text 1 in the binary file",
        ):
            with self.subTest(place=place):
                self.assert_finding(result, "token", commit[:12], place, TOKEN)

    def test_cuts_a_long_line_around_the_first_value(self) -> None:
        commit = self.plant("feat: add a file", {"settings.py": "x" * 100 + f' KEY = "{TOKEN}" ' + "y" * 100 + "\n"})
        result = self.scan_push(commit)
        self.assert_finding(result, "token", commit[:12], "settings.py:1", TOKEN)
        self.assertIn(f'in: …{"x" * 32} KEY = "{TOKEN[:4]}[redacted]" {"y" * 64}…\n', result.output)

    def test_reports_each_kind_of_value_once_for_a_line_and_replaces_each_value(self) -> None:
        commit = self.plant("feat: add a file", {"settings.py": f"KEYS = {TOKEN} {TOKEN} in {fixture.home}/keys\n"})
        result = self.scan_push(commit)
        self.assertEqual(result.output.count(f"token in {commit[:12]} settings.py:1"), 1, result.output)
        expected = f"in: KEYS = {TOKEN[:4]}[redacted] {TOKEN[:4]}[redacted] in $HOME/keys\n"
        self.assertEqual(result.output.count(expected), 2, result.output)


class PathTest(LeakScanTest):
    def test_finds_a_token_in_a_file_name(self) -> None:
        self.assert_planted_finding("token", "path odd", TOKEN, {f"odd\001{TOKEN}.txt": "notes"})

    def test_finds_a_file_that_holds_secrets_by_its_name(self) -> None:
        paths = [".env", "config/.env.local", "id_rsa", "id_ed25519", "cert.p12", "cert.pfx"]
        cases: list[tuple[str, str, Files]] = [(path, "", {path: "VALUE=1\n"}) for path in paths]
        self.assert_planted_findings("secret-file", [*cases, (".env", "", {".env": ""})])

    def test_leaves_out_examples_public_keys_certificates_and_ordinary_files(self) -> None:
        self.assert_planted_clean(
            {
                ".env.example": "API_KEY=your-key-here\n",
                "id_ed25519.pub": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOnlyAnExample tester\n",
                "certs/ca.pem": BEGIN + "CERTIFICATE-----\nMIIBszCCAVmgAwIBAgIUExample\n-----END CERTIFICATE-----\n",
                "src/import.py": "def read(path):\n    return open(path).read()\n",
                "README.md": "# A project\n\nIt imports expenses from a bank's export, and skips the ones it has.\n",
            }
        )


class TagTest(LeakScanTest):
    def scan_tags(self, *names: str) -> Result:
        lines = "".join(f"refs/tags/{name} {git('rev-parse', name)} refs/tags/{name} {ZEROS}\n" for name in names)
        return self.scan("origin", str(self.remote), stdin=lines)

    def tag_object(self, target: str, kind: str, name: str) -> str:
        return (
            f"object {target}\ntype {kind}\ntag {name}\ntagger Tester <t@example.com> 1700000000 +0000\n\n"
            f"Release with KEY {TOKEN}\n"
        )

    def test_finds_a_token_in_a_tags_message_and_leaks_in_its_taggers_name_and_email(self) -> None:
        self.use_copy()
        git("tag", "-a", "v1", "-m", f"Release with KEY {TOKEN}", fixture.start)
        self.assert_finding(self.scan_tags("v1"), "token", "tag v1", "tag v1 message line 1", TOKEN)
        with mock.patch.dict(os.environ, {"GIT_COMMITTER_NAME": f"Tester of {fixture.home}"}):
            git("tag", "-a", "v2", "-m", "Release", fixture.start)
        self.assert_finding(self.scan_tags("v2"), "home-path", "tag v2", "tagger header", str(fixture.home))
        with mock.patch.dict(os.environ, {"GIT_COMMITTER_EMAIL": f"tester+{TOKEN}@example.com"}):
            git("tag", "-a", "v9", "-m", "Release", fixture.start)
        self.assert_finding(self.scan_tags("v9"), "token", "tag v9", "tagger header", TOKEN)

    def test_finds_a_token_in_a_tagged_file_tree_or_tag(self) -> None:
        self.use_copy()
        blob = git("hash-object", "-w", "--stdin", stdin=f'KEY = "{TOKEN}"\n')
        tree = git("mktree", stdin=f"100644 blob {blob}\tsettings.py\n")
        git("tag", "-a", "v3", "-m", "Release", blob)
        git("tag", "-a", "v4", "-m", "Release", tree)
        git("tag", "-a", "v5", "-m", "Release", "v3")
        self.assert_finding(self.scan_tags("v3"), "token", "tag v3", "the tagged file:1", TOKEN)
        self.assert_finding(self.scan_tags("v4"), "token", "tag v4", "the tagged tree/settings.py:1", TOKEN)
        # A push of v3 and v5, which tags v3, scans v3 once.
        result = self.scan_tags("v3", "v5")
        self.assertEqual(result.output.count("token in tag v3 the tagged file:1"), 1, result.output)

    def test_finds_a_token_in_a_pushed_file_or_tree(self) -> None:
        blob = git("hash-object", "-w", "--stdin", stdin=f'KEY = "{TOKEN}"\n')
        tree = git("mktree", stdin=f"100644 blob {blob}\tsettings.py\n")
        lines = f"refs/tags/file {blob} refs/tags/file {ZEROS}\nrefs/tags/tree {tree} refs/tags/tree {ZEROS}\n"
        result = self.scan("origin", str(self.remote), stdin=lines)
        self.assert_finding(result, "token", blob[:12], "the pushed file:1", TOKEN)
        self.assert_finding(result, "token", tree[:12], "the pushed tree/settings.py:1", TOKEN)

    def test_leaves_out_a_tag_of_a_commit_that_the_remote_has(self) -> None:
        self.use_copy()
        git("tag", "-a", "v6", "-m", "Release", fixture.start)
        result = self.scan_tags("v6")
        self.assert_clean(result)
        self.assertIn("found nothing in 0 commits and 1 other object.", result.output)

    def test_finds_a_token_in_a_tag_that_a_merge_holds_and_in_another_header(self) -> None:
        tree = git("rev-parse", f"{fixture.start}^{{tree}}")
        embedded = self.tag_object(fixture.start, "commit", "v7").replace("\n", "\n ").removesuffix(" ")
        commit = git(
            "hash-object", "-t", "commit", "-w", "--literally", "--stdin",
            stdin=f"tree {tree}\nparent {fixture.start}\nauthor Tester <t@example.com> 1700000000 +0000\n"
            f"committer Tester <t@example.com> 1700000000 +0000\nencoding UTF-8\nextra {TOKEN}\n"
            f"gpgsig -----BEGIN PGP SIGNATURE-----\n -----END PGP SIGNATURE-----\nmergetag {embedded}\nfeat: merge\n",
        )  # fmt: skip
        result = self.scan_push(commit)
        self.assert_finding(result, "token", commit[:12], "extra header", TOKEN)
        self.assert_finding(result, "token", commit[:12], "tag v7 message line 1", TOKEN)

    def test_stops_on_a_tag_whose_target_git_lacks(self) -> None:
        missing = "1" * 40
        tag_object = self.tag_object(missing, "blob", "v8")
        tag = git("hash-object", "-t", "tag", "-w", "--literally", "--stdin", stdin=tag_object)
        result = self.scan("origin", str(self.remote), stdin=f"refs/tags/v8 {tag} refs/tags/v8 {ZEROS}\n")
        self.assert_error(result, f"git has no object {missing}")


class PushTest(LeakScanTest):
    def test_skips_the_commits_that_the_remote_has(self) -> None:
        self.use_copy()
        commit = self.plant("feat: add a key", {"settings.py": f'KEY = "{TOKEN}"\n'})
        git("push", "--quiet", "origin", f"{commit}:refs/heads/published")
        self.assert_clean(self.scan_push(commit))
        git("init", "--quiet", "--bare", str(self.remote.parent / "empty.git"))
        git("remote", "add", "empty", str(self.remote.parent / "empty.git"))
        self.assert_finding(self.scan_push(commit, "empty"), "token", commit[:12], "settings.py:1", TOKEN)
        # The remote sha of a line counts as on the remote too.
        child = git("commit-tree", f"{commit}^{{tree}}", "-p", commit, "-m", "feat: keep the key")
        self.assert_clean(self.scan("empty", str(self.remote), stdin=f"refs/heads/x {child} refs/heads/x {commit}\n"))

    def test_sends_nothing_for_a_deletion_an_unchanged_ref_or_a_short_line(self) -> None:
        commit = self.plant("feat: add a key", {"settings.py": f'KEY = "{TOKEN}"\n'})
        lines = f"(delete) {ZEROS} refs/heads/x {commit}\nrefs/heads/y {commit} refs/heads/y {commit}\nrefs/heads/z\n\n"
        result = self.scan("origin", str(self.remote), stdin=lines)
        self.assert_clean(result)
        self.assertIn("found nothing in 0 commits and 0 other objects.", result.output)

    def test_scans_a_push_over_a_remote_commit_that_git_lacks(self) -> None:
        commit = self.plant("feat: add a key", {"settings.py": f'KEY = "{TOKEN}"\n'})
        lines = f"refs/heads/x {commit} refs/heads/x {'2' * 40}\n"
        self.assert_finding(self.scan("origin", str(self.remote), stdin=lines), "token", commit[:12], "settings.py:1")

    def test_counts_a_merge_by_what_it_adds_to_its_first_parent(self) -> None:
        self.use_copy()
        planted = self.plant("feat: add notes", {"notes.md": f"Intro {TOKEN}\n"})
        git("push", "--quiet", "origin", f"{planted}:refs/heads/published")
        side = self.plant("feat: add more notes", {"more.md": "More\n"})
        git("checkout", "--quiet", "--detach", planted)
        git("merge", "--quiet", "--no-ff", "-m", "chore: merge", side)
        self.assert_clean(self.scan_push(git("rev-parse", "HEAD")))

    def test_counts_a_changed_file_by_the_lines_that_its_old_version_lacks(self) -> None:
        self.use_copy()
        files = {"settings.py": f'KEY = "{TOKEN}"\n', "old.txt": "Old\n", "gone.txt": "Gone\n"}
        git("push", "--quiet", "origin", f"{self.plant('feat: add a key', files)}:refs/heads/published")
        write(Path("settings.py"), f'KEY = "{TOKEN}"\nVALUE = 1\n')
        git("mv", "old.txt", "renamed.txt")
        git("rm", "--quiet", "gone.txt")
        git("commit", "--quiet", "-am", "feat: add a value")
        self.assert_clean(self.scan_push(git("rev-parse", "HEAD")))

    def test_scans_a_root_commit_against_the_empty_tree(self) -> None:
        self.use_copy()
        git("checkout", "--quiet", "--orphan", "fresh")
        git("rm", "--quiet", "-rf", ".")
        write(Path("config.txt"), f"cache = {fixture.home}/cache\n")
        git("add", "config.txt")
        git("commit", "--quiet", "-m", "chore: start over")
        commit = git("rev-parse", "HEAD")
        self.assert_finding(self.scan_push(commit), "home-path", commit[:12], "config.txt:1", str(fixture.home))

    def test_skips_the_content_of_a_submodule_but_not_its_path(self) -> None:
        git("checkout", "--quiet", "--detach", fixture.start)
        # The submodule's commit isn't in this repository, as is usual.
        git("update-index", "--add", "--cacheinfo", f"160000,{'4' * 40},vendor/{TOKEN}")
        git("commit", "--quiet", "-m", "feat: add a submodule")
        commit = git("rev-parse", "HEAD")
        self.assert_finding(self.scan_push(commit), "token", commit[:12], f"path vendor/{TOKEN[:4]}[redacted]", TOKEN)
        git("reset", "--quiet", "--hard", fixture.start)

    def test_stops_on_a_pushed_commit_that_git_lacks(self) -> None:
        lines = f"refs/heads/x {'3' * 40} refs/heads/x {ZEROS}\n"
        self.assert_error(self.scan("origin", str(self.remote), stdin=lines), "git cat-file -t failed: ")


class RangeTest(LeakScanTest):
    def test_scans_the_commits_in_a_range(self) -> None:
        commit = self.plant("feat: add a key", {"settings.py": f'KEY = "{TOKENS[2]}"\n'})
        result = self.scan("--range", f"{fixture.start}..{commit}")
        self.assert_finding(result, "token", commit[:12], "settings.py:1", TOKENS[2])
        self.assertIn("found 1 leak in 1 commit.", result.output)
        result = self.scan("--generic", "--range", f"{fixture.start}..{commit}")
        self.assert_finding(result, "token", commit[:12], "settings.py:1", TOKENS[2])

    def test_finds_a_copy_of_a_private_file_in_a_range(self) -> None:
        commit = self.plant("feat: add settings", {"settings.ini": SMALL_FILE})
        result = self.scan("--range", f"{fixture.start}..{commit}")
        self.assert_finding(result, "private-file", commit[:12], "settings.ini")

    def test_leaves_out_the_kinds_that_depend_on_the_machine_with_generic(self) -> None:
        files = {"config.txt": f"cache = {fixture.home}/cache\n", "notes.md": PRIVATE_LINE + "\n"}
        commit = self.plant("feat: add a path", files)
        result = self.scan("--generic", "--range", f"{fixture.start}..{commit}")
        self.assert_clean(result)
        self.assertIn("found nothing in 1 commit.", result.output)

    def test_stops_with_its_usage_when_the_arguments_dont_fit(self) -> None:
        for arguments in ((), ("origin",), ("--range", "a..b", "origin")):
            with self.subTest(arguments=arguments):
                self.assert_error(self.scan(*arguments), "usage: scripts/leak-scan")


class ScriptTest(LeakScanTest):
    def run_script(self, *arguments: str, path: str) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ, PATH=path)
        command = [sys.executable, str(LEAK_SCAN), *arguments]
        return subprocess.run(command, capture_output=True, text=True, env=environment, check=False)

    def test_runs_as_a_script(self) -> None:
        commit = self.plant("feat: add a key", {"settings.py": f'KEY = "{TOKEN}"\n'})
        done = self.run_script("--range", f"{fixture.start}..{commit}", path=os.environ["PATH"])
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn(f"token in {commit[:12]} settings.py:1", done.stdout)

    def test_exits_with_code_2_when_it_fails_on_its_own_error(self) -> None:
        # A stand-in for git names an object format that hashlib doesn't know, and runs git for each other command.
        stand_ins = Path(tempfile.mkdtemp(dir=fixture.base))
        script = f'#!/bin/sh\nif [ "$4" = "--show-object-format" ]; then echo sha999; exit 0; fi\nexec {shutil.which("git")} "$@"\n'
        write(stand_ins / "git", script)
        (stand_ins / "git").chmod(0o755)
        commit = self.plant("feat: add settings", {"settings.ini": SMALL_FILE})
        done = self.run_script(
            "--range", f"{fixture.start}..{commit}", path=f"{stand_ins}{os.pathsep}{os.environ['PATH']}"
        )
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("error: scripts/leak-scan failed: ValueError: ", done.stderr)


class HookTest(LeakScanTest):
    """Tests the pre-push hook and its installer in a clone that holds copies of them and of the scan."""

    def setUp(self) -> None:
        super().setUp()
        self.use_copy()
        self.copy_scripts()

    def copy_scripts(self) -> None:
        """Copies the hook, the installer and the scan into the checkout of the current folder."""
        for name in ("leak-scan", "install-git-hooks", "git-hooks/pre-push", "loop/paths.py"):
            (Path("scripts") / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SCRIPTS / name, Path("scripts") / name)
        shutil.copy2(SCRIPTS.parent / ".python-version", ".python-version")

    def install(self) -> str:
        done = subprocess.run(["scripts/install-git-hooks"], capture_output=True, text=True, check=True)
        return done.stdout

    def push(self, ref: str, loop: Path | None) -> subprocess.CompletedProcess[str]:
        """Pushes HEAD to a ref of the remote. Without a loop folder, the scan reads the main checkout's."""
        # The hook runs with the home folder that the tests run in, where uv finds the Python that runs the scan.
        environment = dict(os.environ, HOME=fixture.real_home, LOOP_DIR=str(loop or ""))
        command = ["git", "push", "origin", f"HEAD:{ref}"]
        return subprocess.run(command, capture_output=True, text=True, env=environment, check=False)

    def remote_has(self, ref: str) -> bool:
        command = ["git", "--git-dir", str(self.remote), "rev-parse", "--verify", "--quiet", ref]
        return subprocess.run(command, capture_output=True, check=False).returncode == 0

    def test_installs_the_hook_once(self) -> None:
        self.assertEqual(self.install(), "git now runs the hooks in scripts/git-hooks.\n")
        self.assertEqual(git("config", "--get", "core.hooksPath"), "scripts/git-hooks")
        config = Path(".git/config").read_text()
        self.assertEqual(self.install(), "git already runs the hooks in scripts/git-hooks.\n")
        self.assertEqual(Path(".git/config").read_text(), config)
        git("config", "core.hooksPath", ".githooks")
        self.assertEqual(
            self.install(), "git now runs the hooks in scripts/git-hooks, instead of the hooks in .githooks.\n"
        )

    def test_blocks_a_push_with_a_token_and_lets_a_clean_push_through(self) -> None:
        self.install()
        no_loop = fixture.base / "no-loop"
        git("checkout", "--quiet", "-B", "work", fixture.start)
        write(Path("settings.py"), f'KEY = "{TOKEN}"\n')
        git("add", "settings.py")
        git("commit", "--quiet", "-m", "feat: add a key")
        done = self.push("refs/heads/hooked", no_loop)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn(f"token in {git('rev-parse', '--short=12', 'HEAD')} settings.py:1", done.stdout)
        self.assertIn("would publish what the lines above show.", done.stderr)
        self.assertIn("Don't push with --no-verify unless the maintainer says so.", done.stderr)
        self.assertFalse(self.remote_has("refs/heads/hooked"))
        write(Path("settings.py"), 'KEY = os.environ["KEY"]\n')
        git("commit", "--quiet", "--amend", "-am", "feat: read the key from the environment")
        done = self.push("refs/heads/hooked", no_loop)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(self.remote_has("refs/heads/hooked"))

    def test_blocks_a_push_from_a_worktree_with_a_line_from_a_folder_that_the_main_checkouts_list_names(self) -> None:
        self.install()
        write(Path(".loop/leak-scan.paths"), f"{fixture.private}\n")
        git("worktree", "add", "--quiet", "--detach", "../worktree", fixture.start)
        os.chdir("../worktree")
        self.copy_scripts()
        write(Path("notes.md"), PRIVATE_LINE + "\n")
        git("add", "notes.md")
        git("commit", "--quiet", "-m", "docs: add the notes")
        done = self.push("refs/heads/notes", None)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn(f"private-line in {git('rev-parse', '--short=12', 'HEAD')} notes.md:1", done.stdout)
        self.assertFalse(self.remote_has("refs/heads/notes"))

    def test_blocks_a_push_that_the_scan_cant_scan(self) -> None:
        self.install()
        write(fixture.base / "broken-loop" / "leak-scan.paths", f"{fixture.base}/no-such-folder\n")
        done = self.push("refs/heads/broken", fixture.base / "broken-loop")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("scripts/leak-scan couldn't scan the commits that the push sends.", done.stderr)
        self.assertFalse(self.remote_has("refs/heads/broken"))
