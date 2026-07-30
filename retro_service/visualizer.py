import logging
import os
import shutil
import uuid
from typing import Dict, List, Optional

from .chemistry import Chem, Draw, RDKIT_AVAILABLE, graphviz


class RouteVisualizer:
    """路线可视化模块，仅服务 /api/plan/download。"""

    @staticmethod
    def generate_image_bytes(plan_data_list: List[Dict]) -> Optional[bytes]:
        if not RDKIT_AVAILABLE or not plan_data_list:
            return None

        task_id = str(uuid.uuid4())
        temp_dir = f"temp_mol_{task_id}"
        os.makedirs(temp_dir, exist_ok=True)
        img_basename = f"route_{task_id}"
        output_path = ""

        dot = graphviz.Digraph(comment="Retro Synthesis Plan")
        dot.attr(rankdir="LR", splines="ortho")
        dot.attr("node", shape="plaintext", fontname="Arial", nodesep="0.5", ranksep="0.5")

        def add_node_to_graph(node: Dict, parent_id: Optional[str] = None) -> None:
            smiles = node.get("smiles", "")
            node_id = str(uuid.uuid4())
            img_path = os.path.join(temp_dir, f"{node_id}.png")

            mol = Chem.MolFromSmiles(smiles) if smiles else None
            if mol:
                Draw.MolToFile(mol, img_path, size=(200, 200), imageType="png")
            else:
                with open(img_path, "wb"):
                    pass

            node_type = node.get("type", "unknown")
            colors = {
                "material": ("#2ecc71", "#eafaf1", "Stock Material"),
                "dead_end": ("#e74c3c", "#fadbd8", "Dead End"),
                "cycle": ("#e74c3c", "#fadbd8", "Cycle"),
                "max_depth": ("#e67e22", "#fdebd0", "Max Depth"),
                "timeout": ("#e67e22", "#fdebd0", "Timeout"),
                "pruned": ("#95a5a6", "#ecf0f1", "Pruned"),
                "invalid_smiles": ("#e74c3c", "#fadbd8", "Invalid SMILES"),
                "target": ("#3498db", "#ebf5fb", "Target"),
                "unknown": ("black", "white", "Intermediate"),
                "intermediate": ("black", "white", "Intermediate"),
            }
            border_col, bg_col, label_txt = colors.get(node_type, colors["unknown"])
            if node_type not in {"material", "dead_end", "cycle", "max_depth", "timeout", "pruned"} and parent_id is None:
                border_col, bg_col, label_txt = colors["target"]

            display_smiles = smiles[:30] + ("..." if len(smiles) > 30 else "")
            html_label = f'''<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" COLOR="{border_col}" BGCOLOR="{bg_col}">
                <TR><TD BORDER="0"><FONT POINT-SIZE="12"><B>{label_txt}</B></FONT></TD></TR>
                <TR><TD BORDER="0"><IMG SRC="{img_path}"/></TD></TR>
                <TR><TD BORDER="0"><FONT POINT-SIZE="9" FACE="Courier New">{display_smiles}</FONT></TD></TR>
            </TABLE>>'''

            dot.node(node_id, label=html_label)
            if parent_id:
                dot.edge(node_id, parent_id, color="#555555", penwidth="1.5")

            for child in node.get("children", []):
                add_node_to_graph(child, node_id)

        try:
            for tree in plan_data_list:
                add_node_to_graph(tree)
            output_path = dot.render(img_basename, format="png", cleanup=True)
            with open(output_path, "rb") as image_file:
                return image_file.read()
        except Exception as exc:
            logging.error("Image generation failed: %s", exc)
            return None
        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            if output_path and os.path.exists(output_path):
                os.remove(output_path)
            if os.path.exists(img_basename):
                os.remove(img_basename)
