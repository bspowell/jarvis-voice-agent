"""The file tools' sandbox: escapes, secrets, and writes that never replace or remove anything.

Every test runs in a temporary folder standing in for your home folder's Documents and Desktop.
"""

import os
from pathlib import Path

import pytest

from jarvis.tools import files
from jarvis.tools.files import (
    MAX_READ_CHARS,
    Refused,
    Sandbox,
    add_to_file,
    create_file,
    describe_file_addition,
    describe_new_file,
    find_files,
    read_file,
)


@pytest.fixture
def home(tmp_path):
    """A pretend home: Documents and Desktop are Jarvis's; Secret is not."""
    for folder in ["Documents", "Desktop", "Secret"]:
        (tmp_path / folder).mkdir()
    return tmp_path


@pytest.fixture
def box(home, monkeypatch):
    monkeypatch.setenv("HOME", str(home))  # so "~" means the pretend home
    sandbox = Sandbox.of([home / "Documents", home / "Desktop"])
    monkeypatch.setattr(files, "sandbox", lambda: sandbox)  # what the tools use
    return sandbox


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# --- Escapes ---
@pytest.mark.parametrize(
    "path",
    [
        "~/Secret/plans.md",  # outside Jarvis's folders
        "~/Documents/../Secret/plans.md",  # `..` climbing out
        "/etc/hosts",
        "~/Documents/.env",  # hidden file
        "~/Documents/.git/config",  # inside a hidden folder
        "~/Documents/aws-credentials.txt",  # secret-looking names
        "~/Desktop/server.pem",
        "~/Desktop/id_ed25519",
        "~/Documents/prod.env",
    ],
)
def test_paths_outside_the_rules_are_refused(box, path):
    with pytest.raises(Refused):
        box.check(path)


def test_a_symlink_pointing_outside_is_refused(box, home):
    write(home / "Secret" / "plans.md", "the real secret")
    (home / "Desktop" / "plans.md").symlink_to(home / "Secret" / "plans.md")
    (home / "Desktop" / "shortcut").symlink_to(home / "Secret")  # a symlinked folder too
    with pytest.raises(Refused, match="outside"):
        box.check("~/Desktop/plans.md")
    with pytest.raises(Refused, match="outside"):
        box.check("~/Desktop/shortcut/plans.md")


def test_a_symlink_to_a_hidden_file_is_refused(box, home):
    write(home / "Documents" / "project" / ".env", "API_KEY=sk-123")
    (home / "Desktop" / "notes.md").symlink_to(home / "Documents" / "project" / ".env")
    with pytest.raises(Refused, match="hidden"):
        box.check("~/Desktop/notes.md")


def test_paths_inside_are_allowed_in_any_spelling(box, home):
    note = write(home / "Documents" / "ideas.md", "x")
    assert box.check("~/Documents/ideas.md") == note.resolve()
    assert box.check("Documents/ideas.md") == note.resolve()  # relative to home
    assert box.check(str(note)) == note.resolve()
    assert box.check("~/Desktop/../Documents/ideas.md") == note.resolve()  # `..` staying inside


# --- Finding and reading ---
def test_find_matches_names_and_what_notes_say(box, home):
    write(home / "Documents" / "trek-trip.md", "Venice in May")
    write(home / "Desktop" / "packing.txt", "tent, stove, trek snacks")
    write(home / "Desktop" / "budget.csv", "trek,100")  # not a note: its text isn't searched
    found = [match["path"] for match in box.find("trek")]
    assert sorted(found) == ["~/Desktop/packing.txt", "~/Documents/trek-trip.md"]


def test_find_skips_hidden_secret_bulky_and_symlinked_places(box, home):
    write(home / "Documents" / ".hidden" / "trek.md", "x")
    write(home / "Documents" / "node_modules" / "trek.md", "x")
    write(home / "Documents" / "trek-secrets.md", "x")
    write(home / "Secret" / "trek.md", "x")
    (home / "Desktop" / "trek-link.md").symlink_to(home / "Secret" / "trek.md")
    assert box.find("trek") == []


def test_find_puts_the_newest_first(box, home):
    old = write(home / "Documents" / "notes-old.md", "x")
    write(home / "Documents" / "notes-new.md", "x")
    os.utime(old, (1_000_000_000, 1_000_000_000))  # 2001
    assert [m["path"] for m in box.find("notes")] == [
        "~/Documents/notes-new.md",
        "~/Documents/notes-old.md",
    ]


def test_reading_a_long_file_cuts_it_short(box, home):
    write(home / "Documents" / "long.md", "a" * (MAX_READ_CHARS + 10))
    result = box.read("~/Documents/long.md")
    assert len(result["text"]) == MAX_READ_CHARS
    assert result["cut_short"] is True


def test_binary_files_are_refused(box, home):
    (home / "Desktop" / "photo.jpg").write_bytes(b"\xff\xd8\xff\x00\x10JFIF")
    with pytest.raises(Refused, match="isn't text"):
        box.read("~/Desktop/photo.jpg")


# --- Writing: create and append only ---
def test_create_makes_a_new_note(box, home):
    result = box.create("~/Desktop/groceries.md", "- milk\n- eggs")
    assert result == {"created": "~/Desktop/groceries.md", "lines": 2}
    assert (home / "Desktop" / "groceries.md").read_text() == "- milk\n- eggs\n"


def test_create_never_replaces_a_file(box, home):
    note = write(home / "Desktop" / "groceries.md", "- bread\n")
    with pytest.raises(Refused, match="already exists"):
        box.create("~/Desktop/groceries.md", "- milk")
    assert note.read_text() == "- bread\n"


def test_only_notes_are_written(box):
    for path in ["~/Desktop/run.sh", "~/Desktop/data.json", "~/Desktop/page.html"]:
        with pytest.raises(Refused, match=r"\.md or \.txt"):
            box.create(path, "x")


def test_add_appends_on_a_new_line(box, home):
    note = write(home / "Desktop" / "groceries.md", "- bread")  # no line break at the end
    result = box.add("~/Desktop/groceries.md", "- milk")
    assert result == {"added_to": "~/Desktop/groceries.md", "lines": 1}
    assert note.read_text() == "- bread\n- milk\n"  # nothing before it changed


def test_add_needs_an_existing_note(box):
    with pytest.raises(Refused, match="create it instead"):
        box.add("~/Desktop/missing.md", "- milk")


# --- The tools the model calls ---
async def test_refusals_reach_the_model_as_a_note(box):
    result = await read_file.ainvoke({"path": "~/Secret/plans.md"})
    assert "outside Jarvis's folders" in result["error"]


async def test_the_tools_use_the_sandbox(box, home):
    await create_file.ainvoke({"path": "~/Documents/ideas.md", "content": "A robot butler"})
    await add_to_file.ainvoke({"path": "~/Documents/ideas.md", "text": "with a dry wit"})
    found = await find_files.ainvoke({"words": "butler"})
    assert [m["path"] for m in found["matches"]] == ["~/Documents/ideas.md"]
    read = await read_file.ainvoke({"path": "~/Documents/ideas.md"})
    assert read["text"] == "A robot butler\nwith a dry wit\n"


def test_approval_words_say_exactly_what_will_be_written():
    create = {
        "name": "create_file",
        "args": {"path": "~/Desktop/g.md", "content": "- milk\n- eggs"},
    }
    add = {"name": "add_to_file", "args": {"path": "~/Desktop/g.md", "text": "- bread"}}
    assert (
        describe_new_file(create, {}, None)
        == "Create the file ~/Desktop/g.md, saying: “- milk / - eggs”"
    )
    assert (
        describe_file_addition(add, {}, None)
        == "Add to the end of the file ~/Desktop/g.md: “- bread”"
    )
