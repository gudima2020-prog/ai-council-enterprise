from backend.control_center.backup import _sha, _canon
def test_backup_hash_is_deterministic():
    assert _sha(_canon({"b":2,"a":1})) == _sha(_canon({"a":1,"b":2}))
