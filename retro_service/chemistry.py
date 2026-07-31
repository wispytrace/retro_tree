from typing import Optional

try:
    from rdkit import Chem
    from rdkit.Chem import Draw

    RDKIT_AVAILABLE = True
except ImportError:  # pragma: no cover - 部署环境没有 RDKit 时仍可提供 JSON 服务
    Chem = None
    Draw = None
    RDKIT_AVAILABLE = False

try:
    import graphviz
except ImportError:  # pragma: no cover - 仅影响路线图渲染
    graphviz = None


def canonicalize_smiles(smiles: str, *, isomeric: bool = False) -> Optional[str]:
    smiles = (smiles or "").strip()
    if not smiles:
        return None
    if not RDKIT_AVAILABLE:
        return smiles
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    return Chem.MolToSmiles(mol, isomericSmiles=isomeric)
