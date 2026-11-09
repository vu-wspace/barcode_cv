"""One box on the packing table: Expected (from BE) vs Scanned (barcode per item track) vs Visual count."""
from collections import Counter, defaultdict


def majority(votes):
    return votes.most_common(1)[0][0] if votes else None


class BoxSession:
    """Keyed by the box barcode (box_id), not by tracker IDs, which switch on occlusion."""

    def __init__(self, box_id=None):
        self.box_id = box_id
        self.items = defaultdict(Counter)  # item track id -> votes of barcodes read on it
        self.first_seen = {}  # item track id -> time first seen, for the "rotate the code" hint
        self.expected = None  # [{item_id, barcodes[], quantity}] from BE; None until loaded / unknown box
        self.loading = False  # checklist request in flight
        self.state = "packing"  # packing -> (Xong, OK) sealing -> (Xong) done
        self.last_compare = None

    def see_item(self, tid, now):
        self.items[tid]  # seen as a segment: counts toward visual count even before its barcode is read
        self.first_seen.setdefault(tid, now)

    def read_item(self, tid, code):
        self.items[tid][code] += 1

    def scanned(self):
        """item_id -> count, +1 per item track (majority barcode). Codes not in the order stay as raw barcodes."""
        lookup = {b: e["item_id"] for e in self.expected or [] for b in e["barcodes"]}
        return Counter(lookup.get(majority(c), majority(c)) for c in self.items.values() if c)

    def unread(self, now=None, after=0.0):
        """Item tracks seen but never decoded (optionally: for at least `after` seconds)."""
        return [t for t, c in self.items.items() if not c and (now is None or now - self.first_seen[t] >= after)]

    def compare(self):
        """Expected vs Scanned, and Scanned vs Visual count (unread items). OK only if everything matches."""
        exp = {e["item_id"]: e["quantity"] for e in self.expected or []}
        got = self.scanned()
        missing = {i: q - got[i] for i, q in exp.items() if got[i] < q}
        extra = {i: n - exp[i] for i, n in got.items() if i in exp and n > exp[i]}
        wrong = {i: n for i, n in got.items() if i not in exp}  # not in the order
        unread = len(self.unread())
        ok = self.expected is not None and not (missing or extra or wrong or unread)
        return {"result": "OK" if ok else "NG", "missing": missing, "extra": extra, "wrong": wrong,
                "unread": unread, "visual_count": len(self.items), "scanned": dict(got),
                "no_checklist": self.expected is None}


if __name__ == "__main__":  # self-check
    s = BoxSession("X55277605433")
    s.expected = [{"item_id": "SALONPAS", "barcodes": ["8935106211321"], "quantity": 2},
                  {"item_id": "TAPE", "barcodes": ["8930000000014"], "quantity": 1}]
    for tid in (1, 2, 3, 4):
        s.see_item(tid, 0)
    s.read_item(1, "8935106211321"); s.read_item(1, "8935106211321"); s.read_item(1, "8934106211321")  # misread outvoted
    s.read_item(2, "8935106211321")
    s.read_item(3, "4006381333931")  # not in the order
    c = s.compare()  # item 4 never read
    assert c["scanned"] == {"SALONPAS": 2, "4006381333931": 1}, c
    assert c["missing"] == {"TAPE": 1} and c["extra"] == {} and c["wrong"] == {"4006381333931": 1}, c
    assert c["unread"] == 1 and c["visual_count"] == 4 and c["result"] == "NG", c
    assert s.unread(now=1, after=2) == [] and s.unread(now=3, after=2) == [4]

    s.read_item(2, "8935106211321")
    ok = BoxSession("B")
    ok.expected = [{"item_id": "A", "barcodes": ["1", "2"], "quantity": 2}]
    ok.see_item(7, 0); ok.read_item(7, "1"); ok.see_item(8, 0); ok.read_item(8, "2")  # two barcodes, same item_id
    assert ok.compare()["result"] == "OK", ok.compare()
    ok.see_item(9, 0); ok.read_item(9, "1")
    assert ok.compare()["extra"] == {"A": 1}
    assert BoxSession("C").compare()["result"] == "NG"  # no checklist from BE -> never OK
    print("ok")
