"""One box on the packing table: Expected (from BE) vs Scanned (barcode per item track) vs Visual count,
plus the model-free rules that route each decoded barcode to an item or to the box."""
from collections import Counter, defaultdict

import cv2
import numpy as np

from barcode_reader import ean13_ok

PRESENT_S = 3.0  # an item not seen for this long has left the box (or was a passing hand) and no longer counts
BOX_VOTES = 2  # reads of the same box barcode (within BOX_WINDOW_S) before it is trusted as box_id
BOX_WINDOW_S = 3.0


def majority(votes):
    return votes.most_common(1)[0][0] if votes else None


def obb_from_mask(polygon):
    """Oriented box (4 corner points, int) = smallest rotated rectangle around a mask outline."""
    return cv2.boxPoints(cv2.minAreaRect(np.asarray(polygon, np.float32))).astype(np.int32)


def inside(polygon, pt):
    return cv2.pointPolygonTest(np.asarray(polygon, np.float32), pt, False) >= 0


class BoxSession:
    """Keyed by the box barcode (box_id), not by tracker IDs, which switch on occlusion."""

    def __init__(self, box_id=None):
        self.box_id = box_id
        self.items = defaultdict(Counter)  # item track id -> votes of barcodes read on it
        self.first_seen = {}  # item track id -> time first seen, for the "rotate the code" hint
        self.last_seen = {}  # item track id -> time last seen in the box
        self.expected = None  # [{item_id, barcodes[], quantity}] from BE; None until loaded / unknown box
        self.loading = False  # checklist request in flight
        self.state = "packing"  # packing -> (Xong, OK) sealing -> (Xong) done
        self.last_compare = None

    def see_item(self, tid, now):
        self.items[tid]  # seen as a segment: counts toward visual count even before its barcode is read
        self.first_seen.setdefault(tid, now)
        self.last_seen[tid] = now

    def read_item(self, tid, code):
        self.items[tid][code] += 1

    def present(self, now=None):
        """Item tracks still in the box (seen within PRESENT_S); all of them when now is None."""
        return {t: c for t, c in self.items.items()
                if now is None or now - self.last_seen.get(t, now) <= PRESENT_S}

    def scanned(self, now=None):
        """item_id -> count, +1 per present item track (majority barcode). Codes not in the order stay raw."""
        lookup = {b: e["item_id"] for e in self.expected or [] for b in e["barcodes"]}
        return Counter(lookup.get(majority(c), majority(c)) for c in self.present(now).values() if c)

    def unread(self, now=None, after=0.0):
        """Present item tracks never decoded (optionally: seen for at least `after` seconds)."""
        return [t for t, c in self.present(now).items()
                if not c and (now is None or now - self.first_seen[t] >= after)]

    def compare(self, now=None):
        """Expected vs Scanned, and Scanned vs Visual count (unread items). OK only if everything matches."""
        exp = {e["item_id"]: e["quantity"] for e in self.expected or []}
        got = self.scanned(now)
        missing = {i: q - got[i] for i, q in exp.items() if got[i] < q}
        extra = {i: n - exp[i] for i, n in got.items() if i in exp and n > exp[i]}
        wrong = {i: n for i, n in got.items() if i not in exp}  # not in the order
        unread = len(self.unread(now))
        ok = self.expected is not None and not (missing or extra or wrong or unread)
        return {"result": "OK" if ok else "NG", "missing": missing, "extra": extra, "wrong": wrong,
                "unread": unread, "visual_count": len(self.present(now)), "scanned": dict(got),
                "no_checklist": self.expected is None}


def assign(session, box_votes, items, live, now, text, c):
    """Route a barcode decoded at full-frame point c (one box at a time on the packing table):
    - on an item's mask -> that item's code (valid EAN-13 only);
    - on a live box but not on an item -> box barcode vote;
    - outside every box: a shipping-label code (not EAN-13) counts as box barcode only while the box is still
      unknown, so a second parcel in the background cannot take over a box being packed.
    Returns True when the code was used."""
    item_tid = next((t for t, p in items if inside(p, c)), None)
    if item_tid is not None:
        if session is None or not ean13_ok(text):
            return False
        if not session.items[item_tid][text]:  # print each code once per item, not every frame
            print(f"[scan] box {session.box_id or '?'}  item #{item_tid}: {text}")
        session.read_item(item_tid, text)
        return True
    on_box = any(inside(o, c) for _, o in live)
    if not on_box and (ean13_ok(text) or (session is not None and session.box_id is not None)):
        return False
    if not any(code == text for _, code in box_votes):
        print(f"[scan] box barcode: {text}")
    box_votes.append((now, text))
    return True


def box_code(box_votes, now, done_ids):
    """Box barcode with >= BOX_VOTES reads in the last BOX_WINDOW_S; recent-only so a new box can take over."""
    while box_votes and now - box_votes[0][0] > BOX_WINDOW_S:
        box_votes.popleft()
    code, n = (Counter(c for _, c in box_votes if c not in done_ids).most_common(1) or [(None, 0)])[0]
    return code if n >= BOX_VOTES else None


if __name__ == "__main__":  # self-check
    from collections import deque

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

    ok = BoxSession("B")
    ok.expected = [{"item_id": "A", "barcodes": ["1", "2"], "quantity": 2}]
    ok.see_item(7, 0); ok.read_item(7, "1"); ok.see_item(8, 0); ok.read_item(8, "2")  # two barcodes, same item_id
    assert ok.compare(now=1)["result"] == "OK", ok.compare(now=1)
    ok.see_item(9, 0); ok.read_item(9, "1")
    assert ok.compare(now=1)["extra"] == {"A": 1}
    assert BoxSession("C").compare()["result"] == "NG"  # no checklist from BE -> never OK

    # Items that leave the box stop counting: scanned-then-removed is missing, a passing hand is not "unread"
    gone = BoxSession("D")
    gone.expected = [{"item_id": "A", "barcodes": ["1"], "quantity": 1}]
    gone.see_item(1, 0); gone.read_item(1, "1")  # scanned, then taken out
    gone.see_item(2, 0)  # hand reaching in, never decoded
    assert gone.compare(now=1)["unread"] == 1  # hand still in the box: unread, NG
    late = gone.compare(now=10)
    assert late["result"] == "NG" and late["missing"] == {"A": 1} and late["unread"] == 0, late
    gone.see_item(1, 9.5)  # put back
    assert gone.compare(now=10)["result"] == "OK"

    # Barcode routing
    box = [(1, np.array([[0, 0], [100, 0], [100, 100], [0, 100]], np.int32))]
    item = [(7, np.array([[10, 10], [40, 10], [40, 40], [10, 40]], np.float32))]
    r, v = BoxSession(), deque()
    assert assign(r, v, item, box, 0, "8935106211321", (20.0, 20.0)) and r.items[7]["8935106211321"] == 1
    assert not assign(r, v, item, box, 0, "8935106211322", (20.0, 20.0))  # bad checksum on an item
    assert not assign(r, v, item, box, 0, "8935106211321", (500.0, 500.0))  # loose EAN-13 outside the box
    assert assign(r, v, item, [], 0, "X55277605433", (500.0, 500.0))  # label, box not detected yet: counts
    assert box_code(v, 0.1, set()) is None  # 1 read < BOX_VOTES
    assign(r, v, item, [], 0.5, "X55277605433", (500.0, 500.0))
    assert box_code(v, 0.6, set()) == "X55277605433"
    assert box_code(v, 0.6, {"X55277605433"}) is None  # finished box still on the table
    r.box_id = "X55277605433"
    assert not assign(r, v, item, box, 1, "X99999999999", (500.0, 500.0))  # other parcel behind: ignored
    assert assign(r, v, item, box, 1, "X99999999999", (50.0, 80.0))  # new label on the box itself: counts
    for t in (2.0, 2.5):
        assign(r, v, item, box, t, "X99999999999", (50.0, 80.0))
    assert box_code(v, 3.4, set()) == "X99999999999"  # old reads expired -> next box takes over
    assert (obb_from_mask(np.array([[0, 0], [10, 0], [10, 5], [0, 5]])).shape == (4, 2))
    print("ok")
