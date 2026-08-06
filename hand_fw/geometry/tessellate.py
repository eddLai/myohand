"""tessellate - export link meshes and hinge-hole candidates from the STEP.

For every component picked in links.yaml (or every named component when
run without it) this writes a world-space STL plus, per component, the
cylindrical faces (axis, origin, radius, area) - the hinge pins/holes
the kinematics stage fits joint axes from.

Usage: venv-geo/bin/python tessellate.py <file.STEP> <outdir>
"""

import json
import math
import os
import sys

from OCP.TDocStd import TDocStd_Document
from OCP.TCollection import TCollection_ExtendedString
from OCP.XCAFApp import XCAFApp_Application
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool
from OCP.TDF import TDF_LabelSequence
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.StlAPI import StlAPI_Writer
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Cylinder
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp

from extract_step import label_name, entry, raw_nauo_names


def cylinders_of(shape):
    """All cylindrical faces: axis dir, a point on the axis, radius, area."""
    cyls = []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face_s(exp.Current())
        exp.Next()
        surf = BRepAdaptor_Surface(face)
        if surf.GetType() != GeomAbs_Cylinder:
            continue
        cyl = surf.Cylinder()
        ax = cyl.Axis()
        loc, dirv = ax.Location(), ax.Direction()
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        cyls.append({
            "radius": round(cyl.Radius(), 3),
            "origin": [round(loc.X(), 3), round(loc.Y(), 3),
                       round(loc.Z(), 3)],
            "axis": [round(dirv.X(), 5), round(dirv.Y(), 5),
                     round(dirv.Z(), 5)],
            "area": round(props.Mass(), 2),
        })
    return cyls


def main(step_path, outdir):
    os.makedirs(outdir, exist_ok=True)
    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    app = XCAFApp_Application.GetApplication_s()
    app.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    if reader.ReadFile(step_path) != IFSelect_RetDone:
        raise SystemExit("STEP read failed")
    reader.Transfer(doc)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    roots = TDF_LabelSequence()
    shape_tool.GetFreeShapes(roots)
    root = roots.Value(1)

    _, nauo = raw_nauo_names(step_path)

    comps = TDF_LabelSequence()
    XCAFDoc_ShapeTool.GetComponents_s(root, comps)
    writer = StlAPI_Writer()
    report = {}
    for i in range(1, comps.Length() + 1):
        label = comps.Value(i)
        inst = label_name(label) or ("comp%d" % i)
        shape = XCAFDoc_ShapeTool.GetShape_s(label)  # world-located
        if shape is None or shape.IsNull():
            continue
        BRepMesh_IncrementalMesh(shape, 0.2, False, 0.35, True)
        stl = os.path.join(outdir, inst + ".stl")
        writer.Write(shape, stl)
        report[inst] = {
            "product": nauo.get(inst),
            "stl": stl,
            "cylinders": cylinders_of(shape),
        }
        print("%-8s %-22s cyl_faces=%d" % (
            inst, nauo.get(inst), len(report[inst]["cylinders"])))
    with open(os.path.join(outdir, "cylinders.json"), "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
