from rmzotero.tablet.cloud import MemoryCloud
from rmzotero.tablet.index import Entry, doc_index, parse_index, root_index
from rmzotero.tablet.tree import BlobCache, CloudTree, new_metadata


def test_parse_rmapi_fixture_indented():
    index = b"""4
\t0:.:2:1823419036
\t0f83178c4ebe6a60fae0360b74916ee9e1faa5de1c56ab3481eccdc5cb98754f:0:fe0039fb-56a0-4561-a36f-a820f0009622.content:0:993
\t17eca6c9a540c993f5f5506bb09b7a40993c02fa8f065b1a6a442e412cf2fd04:0:fe0039fb-56a0-4561-a36f-a820f0009622.metadata:0:320"""
    schema, entries = parse_index(index)
    assert schema == "4"
    assert [e.size for e in entries] == [993, 320]


def test_doc_index_hash_is_hash_of_binary_hashes():
    import hashlib

    a = Entry(hash="aa" * 32, name="x.metadata", size=1)
    b = Entry(hash="bb" * 32, name="x.content", size=2)
    h, body = doc_index([a, b])
    assert body.decode().splitlines()[1].endswith("x.content:0:2")  # sorted by name
    assert h == hashlib.sha256(bytes.fromhex("bb" * 32) + bytes.fromhex("aa" * 32)).hexdigest()
    rh, rbody = root_index([Entry(hash=h, name="x", size=3, subfiles=2)])
    assert rbody.decode().splitlines()[:2] == ["4", "0:.:1:3"]
    assert rh == hashlib.sha256(rbody).hexdigest()


def test_stage_commit_and_mirror(tmp_path):
    cloud = MemoryCloud()
    t1 = CloudTree(cloud, BlobCache(tmp_path / "a"))
    t1.mirror()
    t1.stage("folder1", metadata=new_metadata("Zotero", "", "CollectionType"), content={})
    t1.stage("doc1", metadata=new_metadata("Paper", "folder1"), content={"fileType": "pdf"}, files={".pdf": b"%PDF-1.4"})
    assert t1.commit() == []
    t2 = CloudTree(cloud, BlobCache(tmp_path / "b"))
    assert t2.mirror()
    assert set(t2.docs) == {"folder1", "doc1"}
    assert t2.path_of("doc1") == ["Zotero"]
    assert t2.read(t2.docs["doc1"].file(".pdf")) == b"%PDF-1.4"


def test_conflict_drops_only_changed_docs(tmp_path):
    cloud = MemoryCloud()
    t1 = CloudTree(cloud, BlobCache(tmp_path / "a"))
    t1.mirror()
    t1.stage("a", metadata=new_metadata("A", ""), content={})
    t1.stage("b", metadata=new_metadata("B", ""), content={})
    t1.commit()
    # "tablet" edits doc a
    tablet = CloudTree(cloud, BlobCache(tmp_path / "t"))
    tablet.mirror()
    tablet.stage("a", files={"/p1.rm": b"strokes"})
    tablet.commit()
    # service edits a and b based on stale view
    t1.stage("a", metadata=dict(t1.docs["a"].metadata, visibleName="A2"))
    t1.stage("b", metadata=dict(t1.docs["b"].metadata, visibleName="B2"))
    dropped = t1.commit()
    assert dropped == ["a"]
    t3 = CloudTree(cloud, BlobCache(tmp_path / "c"))
    t3.mirror()
    assert t3.docs["a"].name == "A" and "a/p1.rm" in t3.docs["a"].entries
    assert t3.docs["b"].name == "B2"
