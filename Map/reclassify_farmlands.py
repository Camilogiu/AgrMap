import os
import re
import warnings
from typing import Dict, Tuple, Optional, List

import geopandas as gpd
import pandas as pd


def _normalize_key(s: str) -> str:
    s2 = s.strip().lower()
    # normalize repeated spaces
    s2 = re.sub(r"\s+", " ", s2)
    return s2


def list_unique_crop(input_dir: str, input_shp: str = "OSM_farmland.shp", field: str = "crop") -> List[str]:
    """Read the shapefile and return a sorted list of unique values in the `field` column.

    Args:
        input_dir: folder containing the shapefile.
        input_shp: shapefile name (default: OSM_farmland.shp).
        field: column to list (default: "crop"; use "crop_class" on farmlands_reclassified.shp).

    Returns:
        Sorted list of unique strings (as they appear in the attribute table).
    """
    path = os.path.join(input_dir, input_shp)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Shapefile not found: {path}")

    gdf = gpd.read_file(path)
    if field not in gdf.columns:
        raise KeyError(f"'{field}' column not found in shapefile")

    vals = gdf[field].dropna().astype(str).map(lambda x: x.strip()).unique().tolist()
    vals_sorted = sorted(vals, key=lambda s: s.lower())
    return vals_sorted


def reclassify_farmlands(input_dir: str,
                         mapping: Dict[Optional[str], Tuple[Optional[str], Optional[str]]],
                         input_shp: str = "OSM_farmland.shp",
                         output_shp: str = "farmlands_reclassified.shp",
                         crop_field: str = "crop",
                         irrigation_field: str = "irrigation",
                         output_field: str = "crop_class",
                         unknown_value: Optional[str] = None,
                         irrigated_keywords: Tuple[str, ...] = ("pivot",)) -> gpd.GeoDataFrame:
    """Reclassify farmland polygons into `crop_class` based on `crop` and `irrigation`.

    Args:
        input_dir: folder containing the input shapefile.
        mapping: dict mapping crop strings -> (irrigated_class, rainfed_class), defined by the
            user in FarmlandClassification.ipynb (USER_MAPPING). The key None is used for
            polygons with an empty crop field.
        input_shp: input shapefile filename.
        output_shp: output shapefile filename to write inside `input_dir`.
        crop_field: name of the crop column.
        irrigation_field: name of the irrigation column.
        output_field: name of the new column to create.
        unknown_value: value to use when no mapping found (default: None).
        irrigated_keywords: substrings in `irrigation_field` that mark a polygon as irrigated
            (default: ("pivot",)).

    Returns:
        The GeoDataFrame with the new `output_field` column (and also writes a shapefile).
    """
    none_mapping = mapping.get(None)
    mapping_norm = {}
    for k, v in mapping.items():
        if k is None:
            continue
        mapping_norm[_normalize_key(str(k))] = v

    in_path = os.path.join(input_dir, input_shp)
    if not os.path.exists(in_path):
        raise FileNotFoundError(f"Shapefile not found: {in_path}")

    gdf = gpd.read_file(in_path)
    if crop_field not in gdf.columns:
        raise KeyError(f"'{crop_field}' column not found in shapefile")
    if irrigation_field not in gdf.columns:
        warnings.warn(f"'{irrigation_field}' column not found; treating as non-pivot for all rows")

    keywords = [k.lower() for k in irrigated_keywords]

    def _is_pivot(irrig_val) -> bool:
        if pd.isna(irrig_val):
            return False
        val = str(irrig_val).lower()
        return any(k in val for k in keywords)

    def _resolve(s_crop: str, irrig_val) -> Optional[str]:
        if s_crop is None or (isinstance(s_crop, float) and pd.isna(s_crop)):
            if none_mapping is None:
                return unknown_value
            pivot_class, nonpivot_class = none_mapping
            return pivot_class if _is_pivot(irrig_val) else nonpivot_class

        s = _normalize_key(str(s_crop))

        # 1) exact full-string match
        if s in mapping_norm:
            pivot_class, nonpivot_class = mapping_norm[s]
        else:
            # 2) try tokens separated by , or ;
            tokens = [t.strip() for t in re.split(r"[,;]", s) if t.strip()]
            pivot_class = nonpivot_class = None
            for t in tokens:
                t_norm = _normalize_key(t)
                if t_norm in mapping_norm:
                    pivot_class, nonpivot_class = mapping_norm[t_norm]
                    break
            # 3) try individual words inside tokens
            if pivot_class is None:
                for t in tokens:
                    for w in t.split():
                        w_norm = _normalize_key(w)
                        if w_norm in mapping_norm:
                            pivot_class, nonpivot_class = mapping_norm[w_norm]
                            break
                    if pivot_class is not None:
                        break

        is_piv = _is_pivot(irrig_val)
        if is_piv:
            return pivot_class if pivot_class is not None else unknown_value
        else:
            return nonpivot_class if nonpivot_class is not None else unknown_value

    # create the new column
    gdf[output_field] = gdf.apply(lambda row: _resolve(row.get(crop_field), row.get(irrigation_field)), axis=1)

    out_path = os.path.join(input_dir, output_shp)
    # write shapefile (ESRI Shapefile driver)
    try:
        gdf.to_file(out_path, driver="ESRI Shapefile")
    except Exception as e:
        raise RuntimeError(f"Failed to write output shapefile {out_path}: {e}")

    return gdf
