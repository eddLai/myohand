"""extract_step - dump the RH56F1 STEP assembly to assembly_inventory.json.

Two passes over the vendor file:
  1. raw text: PRODUCT names (GBK-encoded in this file) keyed by STEP id
  2. OCP/XCAF: the instance tree with world-space bounding box, volume
     and centre of mass per component - the numbers links.yaml labeling
     works from

Usage: venv-geo/bin/python extract_step.py <file.STEP> <out.json>
"""

import json
import re
import sys

from OCP.TDocStd import TDocStd_Document
from OCP.TCollection import TCollection_ExtendedString
from OCP.XCAFApp import XCAFApp_Application
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool
from OCP.TDF import TDF_LabelSequence, TDF_Tool
from OCP.TDataStd import TDataStd_Name
from OCP.TCollection import TCollection_AsciiString
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp


def _gbk(raw):
    try:
        return raw.decode("gbk")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def raw_nauo_names(path):
    """NAUO instance name -> product name, via the PD -> PDF -> PRODUCT
    chain in the raw text (names are GBK in this vendor's file)."""
    data = open(path, "rb").read()
    product = {int(m.group(1)): _gbk(m.group(2)) for m in re.finditer(
        rb"#(\d+)\s*=\s*PRODUCT\s*\(\s*'([^']*)'", data)}
    pdf = {int(m.group(1)): int(m.group(2)) for m in re.finditer(
        rb"#(\d+)\s*=\s*PRODUCT_DEFINITION_FORMATION[A-Z_]*\s*\("
        rb"[^;]*?#(\d+)", data)}
    pd = {int(m.group(1)): int(m.group(2)) for m in re.finditer(
        rb"#(\d+)\s*=\s*PRODUCT_DEFINITION\s*\([^;]*?#(\d+)\s*,", data)}
    nauo = {}
    for m in re.finditer(
            rb"#\d+\s*=\s*NEXT_ASSEMBLY_USAGE_OCCURRENCE\s*\(\s*'([^']*)'"
            rb"\s*,[^;]*?#(\d+)\s*,\s*#(\d+)", data):
        child_pd = int(m.group(3))
        prod = product.get(pdf.get(pd.get(child_pd)))
        nauo[m.group(1).decode("latin-1")] = prod
    return product, nauo


def label_name(label):
    attr = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), attr):
        try:
            s = TCollection_AsciiString(attr.Get()).ToCString()
        except Exception:
            return None
        # OCCT stored the GBK bytes as 8-bit chars; recover the CJK text
        try:
            return s.encode("latin-1").decode("gbk")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return s
    return None


def entry(label):
    s = TCollection_AsciiString()
    TDF_Tool.Entry_s(label, s)
    return s.ToCString()


def shape_stats(shape):
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    com = props.CentreOfMass()
    return {
        "bbox_mm": [round(v, 2) for v in
                    (xmin, ymin, zmin, xmax, ymax, zmax)],
        "volume_mm3": round(props.Mass(), 1),
        "com_mm": [round(com.X(), 2), round(com.Y(), 2), round(com.Z(), 2)],
    }


def walk(shape_tool, label, depth, out):
    node = {
        "entry": entry(label),
        "name": label_name(label),
        "depth": depth,
        "is_assembly": XCAFDoc_ShapeTool.IsAssembly_s(label),
        "is_reference": XCAFDoc_ShapeTool.IsReference_s(label),
    }
    shape = XCAFDoc_ShapeTool.GetShape_s(label)
    if shape is not None and not shape.IsNull():
        node.update(shape_stats(shape))
    out.append(node)
    # a reference label's children live on the label it refers to
    from OCP.TDF import TDF_Label
    ref = TDF_Label()
    if XCAFDoc_ShapeTool.GetReferredShape_s(label, ref):
        node["refers_to"] = entry(ref)
        node["ref_name"] = label_name(ref)
        label = ref
    comps = TDF_LabelSequence()
    XCAFDoc_ShapeTool.GetComponents_s(label, comps)
    for i in range(1, comps.Length() + 1):
        walk(shape_tool, comps.Value(i), depth + 1, out)


def main(step_path, out_path):
    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    app = XCAFApp_Application.GetApplication_s()
    app.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)

    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    if reader.ReadFile(step_path) != IFSelect_RetDone:
        raise SystemExit("STEP read failed")
    if not reader.Transfer(doc):
        raise SystemExit("STEP transfer failed")

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    roots = TDF_LabelSequence()
    shape_tool.GetFreeShapes(roots)

    nodes = []
    for i in range(1, roots.Length() + 1):
        walk(shape_tool, roots.Value(i), 0, nodes)

    product, nauo = raw_nauo_names(step_path)
    for n in nodes:
        pn = nauo.get(n.get("name") or "")
        if pn:
            n["product_name"] = pn

    inventory = {
        "source": step_path.split("/")[-1],
        "products": {str(k): v for k, v in product.items()},
        "nodes": nodes,
    }
    with open(out_path, "w") as f:
        json.dump(inventory, f, ensure_ascii=False, indent=1)
    print("nodes:", len(nodes))
    for n in nodes:
        pad = "  " * n["depth"]
        vol = n.get("volume_mm3")
        com = n.get("com_mm")
        print("%s%s (%s) [%s]%s vol=%s com=%s" % (
            pad, n.get("name"), n.get("product_name") or n.get("ref_name"),
            n["entry"], " ASM" if n["is_assembly"] else "", vol, com))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
