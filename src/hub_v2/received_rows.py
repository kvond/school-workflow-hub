"""Finite authenticated older row layouts, without lending absent tasks."""
from copy import deepcopy

SIZE = [9144000, 5143500]
SHIFTED = {
    2: "Police Confession from suspect", 3: "Paint transferred to a vehicle",
    4: "Gunshot residue", 5: "Glass fragments", 6: "A thief caught stealing from Walmart",
    7: "Body fluids", 8: "DNA",
}
SOURCE_Y = {2: 1931194, 3: 2357438, 4: 2783681, 5: 3209925,
            6: 3636169, 7: 4062413, 8: 4488656}


def base(marker):
    """Only the reviewed finite variants may use their exact teacher descriptor."""
    data = marker.get("binding", {})
    mode = data.get("received_variant")
    if mode is None:
        return marker
    if (marker.get("code") != "F05b" or marker.get("category") != "NOTES"
            or not marker.get("standalone") or data.get("kind") != "conditional-response-row"
            or data.get("page_size") != SIZE):
        return None
    item = data.get("item", {})
    if (not isinstance(item, dict) or not isinstance(data.get("fields"), list)
            or not all(isinstance(f, dict) for f in data["fields"])):
        return None
    identifier = marker.get("id", "")
    valid = (mode in ("original-ransom-note", "original-ransom-note-shifted")
             and identifier == "EVIDENCE-ROW-01" and item.get("text") == "Signed Ransom Note"
             and item.get("box") == [619125, 1504950, 3238500, 426300]
             and [f.get("box") for f in data["fields"]] ==
             [[4143375, 1504950, 2047800, 426300], [6477000, 1504950, 2047800, 426300]])
    valid |= (mode == "original-shifted-row" and any(identifier == "EVIDENCE-ROW-%02d" % number
              and item.get("text") == text and item.get("box") == [619125, SOURCE_Y[number], 3238500, 426300]
              and [f.get("box") for f in data["fields"]] ==
              [[4143375, SOURCE_Y[number], 2047800, 426300], [6477000, SOURCE_Y[number], 2047800, 426300]]
              for number, text in SHIFTED.items()))
    valid |= (mode == "original-primary-only-row14" and identifier == "EVIDENCE-ROW-14"
              and item.get("text") == "A hit and run accident caught on film"
              and item.get("box") == [619125, 2895600, 3238500, 285900]
              and [f.get("box") for f in data.get("fields", [])] ==
              [[4000500, 2895600, 1762200, 285900], [6334125, 2895600, 2000100, 285900]])
    if not valid:
        return None
    original = deepcopy(marker)
    original["binding"].pop("received_variant")
    return original


def regions(marker, source):
    """The retained item/fields only; no answer and no secondary box invented."""
    item, headers, fields = deepcopy(source)
    mode = marker.get("binding", {}).get("received_variant")
    if mode in ("original-ransom-note", "original-ransom-note-shifted"):
        item["text"] = "Ransom Note"
    if mode in ("original-shifted-row", "original-ransom-note-shifted"):
        for region in [item] + fields:
            region["box"] = [region["box"][0] - 142875, region["box"][1] + 100, *region["box"][2:]]
    if mode == "original-primary-only-row14":
        fields = fields[:1]
    return item, headers, fields
