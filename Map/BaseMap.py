import os
import glob
import inspect
import re
import shutil
import tempfile
import requests
import pandas as pd
import numpy as np
import rasterio
import geopandas as gpd
gpd.options.io_engine = "fiona"
from shapely.geometry import Point
from rasterio.features import rasterize
from rasterio.warp import calculate_default_transform
import rasterio.transform
from rasterio.mask import mask
from rasterio.warp import reproject, Resampling
from pyproj import CRS
from scipy.spatial import cKDTree

ROOT_DIR = os.path.abspath(os.curdir)
Data_dir = os.path.join(ROOT_DIR, "Data")
Input_data = os.path.join(Data_dir, "00.InputData")

class Raster:
    def __init__(self):
        pass

        """
        This class contains all the methods needed for reclassifying,
        clipping, reprojecting, converting and filtering all the rasters
        used for the analysis. 

        """

    def reclassify_land_cover(Country_name = str, input_raster=None, output_dir=None, cropland_value=None, out_nodata=255):
        """
        Reclassify a land cover raster into a binary cropland raster.

        Parameters
        ----------
        input_raster : str, optional
            Path to input raster. If None, the first .tif found in input_dir is used.
        output_dir : str
            Directory where the reclassified raster will be saved.
        cropland_value : int, list, or tuple
            Pixel value(s) representing cropland in the input raster.
            Can be a single value (e.g., 1) or multiple values (e.g., [10, 11] or (10, 11)).
        out_nodata : int
            Nodata value for the output raster.

        Returns
        -------
        str
            Path to the output raster.
        """
        # Ensure cropland_value is a list for flexible handling of single or multiple values
        if cropland_value is not None and not isinstance(cropland_value, (list, tuple)):
            cropland_value = [cropland_value]
        
        if input_raster is None:
            tif_files = sorted(glob.glob(os.path.join(Input_data, "*.tif")))
            if not tif_files:
                raise FileNotFoundError(f"No .tif files found in {Input_data}")
            input_raster = tif_files[0]

        print(f"Using input: {input_raster}")

        with rasterio.open(input_raster) as src:
            data = src.read(1)
            src_nodata = src.nodata

            out = np.zeros_like(data, dtype=np.uint8)
            # Use np.isin() to handle single or multiple cropland values
            if cropland_value is not None:
                out[np.isin(data, cropland_value)] = 1

            if src_nodata is not None:
                out[data == src_nodata] = out_nodata

            meta = src.meta.copy()
            meta.update(
                driver="GTiff",
                dtype=rasterio.uint8,
                count=1,
                nodata=(out_nodata if src_nodata is not None else None),
                compress="lzw",
            )

        output_dir = os.path.join(Data_dir, "01.BaseCroplandLayer")
        os.makedirs(output_dir, exist_ok=True)

        basename = Country_name #os.path.splitext(os.path.basename(input_raster))[0]
        output_raster = os.path.join(output_dir, f"{Country_name}_reclassified.tif")

        with rasterio.open(output_raster, "w", **meta) as dst:
            dst.write(out, 1)

        print(f"Reclassified {os.path.basename(input_raster)} -> {os.path.basename(output_raster)}")
        return output_raster
    
    def raster_to_points(Country_name=str, input_tif=None, output_dir=None, value_column="value"):
        """
        Convert raster pixels (non-nodata) into point GeoDataFrames with coordinates.

        Parameters
        ----------
        Country_name : str
            Country code for naming outputs.
        input_tif : str
            Path to the input tif file. If None, uses default naming convention.
        output_dir : str
            Folder where output GeoPackages will be saved.
        value_column : str
            Name of attribute column storing raster values.

        Returns
        -------
        list
            Paths to created GeoPackage files.
        """
        output_dir = os.path.join(Data_dir, "01.BaseCroplandLayer")
        
        if input_tif is None:
            input_tif = os.path.join(Data_dir, "01.BaseCroplandLayer", f"{Country_name}_reclassified_nod.tif")
        
        if not os.path.exists(input_tif):
            raise FileNotFoundError(f"File not found: {input_tif}")

        print(f"Using input: {input_tif}")

        with rasterio.open(input_tif) as src:
            data = src.read(1)
            transform = src.transform
            src_nodata = src.nodata

            if src_nodata is None:
                raise ValueError(f"{input_tif} does not define a nodata value")

            rows, cols = np.where(data != src_nodata)
            if len(rows) == 0:
                raise ValueError(f"No valid pixels found in {input_tif}")

            xs, ys = rasterio.transform.xy(transform, rows, cols, offset="center")
            values = data[rows, cols]

            geometry = [Point(x, y) for x, y in zip(xs, ys)]
            gdf = gpd.GeoDataFrame(
                {
                    #value_column: values,
                    "x": xs,
                    "y": ys
                },
                geometry=geometry,
                crs=src.crs
            )

        out_fp = os.path.join(output_dir, f"{Country_name}.gpkg")
        
        if os.path.exists(out_fp):
            os.remove(out_fp)
        
        gdf.to_file(
            out_fp,
            driver="GPKG",
            layer=Country_name,
            layer_options={"SPATIAL_INDEX": "NO"},
            engine="fiona",
        )
        
        print(f"Saved point vector: {os.path.basename(out_fp)} with {len(gdf)} points")

        return [out_fp]

    def validate_cropland_feasibility(
            Country_name=str,
            FAOSTAT_country_name=str,
            cropland_value=None,
            land_cover_raster=None,
            harvested_csv=None,
    ):
        """
        Sanity-check the land cover raster against FAOSTAT before running the rest of the
        pipeline. For each cropland_value, prints the pixel count/area, compares the total
        cropland area in the raster against the national FAOSTAT harvested area, and warns
        if the raster doesn't have enough cropland capacity to hold it (a prerequisite for
        Step 7 calibration/redistribution to succeed without raising an error).

        Parameters
        ----------
        Country_name : str
        FAOSTAT_country_name : str
            Passed through to Map.calculate_national_statistics.
        cropland_value : int or sequence of int
            Land cover pixel value(s) considered cropland (the 'cropland_value' User Setting).
        land_cover_raster : str, optional
            Defaults to Input_data/{Country_name}_LandCover.tif.
        harvested_csv : str, optional
            Passed through to Map.calculate_national_statistics (which FAOSTAT CSV to use).

        Returns
        -------
        dict with keys: total_area_ha, faostat_total_ha, ratio, passed (bool or None if the
        National_harvested_{Country_name}.csv could not be found/read).
        """
        if land_cover_raster is None:
            land_cover_raster = os.path.join(Input_data, f'{Country_name}_LandCover.tif')
        if cropland_value is None:
            raise ValueError("cropland_value must be provided (the land cover pixel value(s) treated as cropland).")

        print("\n" + "=" * 70)
        print(f"CROPLAND VALIDATION FOR {Country_name}".center(70))
        print("=" * 70)

        with rasterio.open(land_cover_raster) as src:
            data = src.read(1)
            transform = src.transform

            pixel_width = abs(transform.a)
            pixel_height = abs(transform.e)
            pixel_area_m2 = pixel_width * pixel_height

            cropland_values = cropland_value if isinstance(cropland_value, (list, tuple)) else [cropland_value]

            print(f"\nPixel size: {pixel_width:.1f}m × {pixel_height:.1f}m = {pixel_area_m2:,.0f} m²")
            print(f"Cropland values to check: {cropland_values}\n")

            total_pixels = 0
            total_area_m2 = 0

            for value in cropland_values:
                pixel_count = np.sum(data == value)
                area_m2 = pixel_count * pixel_area_m2
                area_ha = area_m2 / 10000
                total_pixels += pixel_count
                total_area_m2 += area_m2
                print(f"  Cropland value {value}: {pixel_count:,} pixels = {area_ha:>12,.0f} ha ({area_m2:.2e} m²)")

            total_area_ha = total_area_m2 / 10000
            print(f"\n  {'TOTAL FROM RASTER':.<30} {total_pixels:>15,} pixels = {total_area_ha:>12,.0f} ha ({total_area_m2:.2e} m²)")

        print(f"\nReading FAOSTAT data for {FAOSTAT_country_name}...")
        result = {"total_area_ha": total_area_ha, "faostat_total_ha": None, "ratio": None, "passed": None}

        # Use a scratch directory so this early sanity check doesn't create 03.Final/ or
        # leave behind National_*_{Country_name}.csv files before Step 7 actually runs.
        with tempfile.TemporaryDirectory() as tmp_dir:
            Map.calculate_national_statistics(
                Country_name=Country_name,
                faostat_country_name=FAOSTAT_country_name,
                harvested_csv=harvested_csv,
                out_dir=tmp_dir,
            )
            national_csv_path = os.path.join(tmp_dir, f"National_harvested_{Country_name}.csv")
            national_df = pd.read_csv(national_csv_path) if os.path.exists(national_csv_path) else None

        if national_df is not None:

            area_col = None
            for col in national_df.columns:
                if 'area' in col.lower():
                    area_col = col
                    break
            if area_col is None:
                area_col = national_df.columns[1]

            faostat_total_ha = national_df[area_col].sum()
            faostat_total_m2 = faostat_total_ha * 10000

            print(f"  {'TOTAL FROM FAOSTAT':.<30} {faostat_total_ha:>12,.0f} ha ({faostat_total_m2:.2e} m²)")
            print("\n" + "=" * 70)

            ratio = total_area_m2 / faostat_total_m2 if faostat_total_m2 > 0 else 0
            passed = total_area_m2 >= faostat_total_m2

            if passed:
                print("✓ VALIDATION PASSED")
                print(f"  Raster area is {ratio:.2%} of FAOSTAT total (≥100%)")
                print("  ➜ Crop calibration is FEASIBLE")
            else:
                print("✗ VALIDATION WARNING")
                print(f"  Raster area is only {ratio:.2%} of FAOSTAT total (<100%)")
                print("  ➜ Crop calibration may be UNFEASIBLE")
                print("\n  Recommendations:")
                print("  1. Consider using a more up-to-date land cover map")
                print("  2. Try different cropland_value(s) that better represent actual cropland")
                print("  3. Check if the land cover raster aligns with your study area")

            print("=" * 70 + "\n")

            result.update({"faostat_total_ha": faostat_total_ha, "ratio": ratio, "passed": passed})
        else:
            print(f"Warning: National_harvested_{Country_name}.csv could not be generated from the FAOSTAT data.")

        return result

    def create_subnational_shapefile(
            Country_name=str,
            admin_shapefile=None,
            target_crs=None,
            output_dir=None,
            admin_level: int = None,
    ):
        """
        Builds the SubNational_reprojected.shp used downstream (rasterize_admin_boundaries,
        mapspam_filter_crops's admin-level pass, SubNationalStat.ipynb) from a raw admin
        boundary shapefile.

        admin_level (or the notebook's Calibration_method, read automatically if admin_level
        is not passed) selects the default input file and which columns are kept:
         - 0: nothing to do (no sub-national calibration); prints a message and returns None.
         - 1 or 2: admin_shapefile defaults to Input_data/{country_name_lower}_admin{admin_level}.shp
           (e.g. 'zmb_admin2.shp' for Country_name='ZMB', admin_level=2).
           Columns kept: 'adm1_name' always, plus 'adm2_name' if admin_level > 1, plus the
           area column (named 'area_sqkm' in the source files seen so far — kept under that
           name, not renamed to 'area', since rasterize_admin_boundaries/mapspam_filter_crops
           already de-duplicate on 'area_sqkm'), 'center_lat', 'center_lon', and 'geometry'.

        target_crs : optional
            Defaults to the notebook's 'crs_proj' (read automatically from the caller's scope
            if not passed), the projected CRS used throughout the pipeline.

        Writes Input_data/SubNational_reprojected.shp.

        Returns the output shapefile path, or None if admin_level is 0.
        """
        if admin_level is None:
            frame = inspect.currentframe()
            try:
                caller = frame.f_back if frame is not None else None
                while caller is not None:
                    if "Calibration_method" in caller.f_locals:
                        admin_level = caller.f_locals["Calibration_method"]
                        break
                    if "Calibration_method" in caller.f_globals:
                        admin_level = caller.f_globals["Calibration_method"]
                        break
                    caller = caller.f_back
            finally:
                del frame
        admin_level = int(admin_level) if admin_level is not None else 0

        if admin_level <= 0:
            print("Calibration_method is 0 (adm0); no sub-national shapefile needed, skipping.")
            return None

        if output_dir is None:
            output_dir = Input_data
        os.makedirs(output_dir, exist_ok=True)

        if admin_shapefile is None:
            admin_shapefile = os.path.join(Input_data, f"{Country_name.lower()}_admin{admin_level}.shp")
        if not os.path.exists(admin_shapefile):
            raise FileNotFoundError(
                f"Admin boundary shapefile not found: {admin_shapefile}. Calibration_method "
                f"is set to {admin_level} — check that it matches the admin level of the "
                f"shapefile you added, or adjust Calibration_method/admin_shapefile."
            )

        if target_crs is None:
            frame = inspect.currentframe()
            try:
                caller = frame.f_back if frame is not None else None
                while caller is not None:
                    if "crs_proj" in caller.f_locals:
                        target_crs = caller.f_locals["crs_proj"]
                        break
                    if "crs_proj" in caller.f_globals:
                        target_crs = caller.f_globals["crs_proj"]
                        break
                    caller = caller.f_back
            finally:
                del frame
        if target_crs is None:
            raise ValueError("target_crs was not provided and 'crs_proj' was not found in the caller's scope.")

        admin = gpd.read_file(admin_shapefile)

        cols_lower = {c.lower(): c for c in admin.columns}
        area_col = cols_lower.get("area_sqkm", cols_lower.get("area"))
        keep_cols = ["adm1_name"]
        if admin_level > 1:
            keep_cols.append("adm2_name")
        for required in [area_col, "center_lat", "center_lon"]:
            if required is None or required not in admin.columns:
                raise ValueError(
                    f"Expected columns 'adm1_name'"
                    f"{', adm2_name' if admin_level > 1 else ''}, 'area_sqkm'/'area', "
                    f"'center_lat', 'center_lon' in {admin_shapefile}; found {admin.columns.tolist()}"
                )
        keep_cols += [area_col, "center_lat", "center_lon"]

        missing = [c for c in keep_cols if c not in admin.columns]
        if missing:
            raise ValueError(f"Missing expected column(s) {missing} in {admin_shapefile}")

        admin = admin[keep_cols + ["geometry"]].copy()
        admin = admin.to_crs(target_crs)

        output_fp = os.path.join(output_dir, "SubNational_reprojected.shp")
        if os.path.exists(output_fp):
            for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qmd"):
                leftover = os.path.splitext(output_fp)[0] + ext
                if os.path.exists(leftover):
                    os.remove(leftover)
        admin.to_file(output_fp)

        print(f"Saved: {output_fp}")
        return output_fp

    def rasterize_admin_boundaries(
            Country_name=str,
            reference_raster=None,
            admin_shapefile=None,
            target_vector=None,
            output_dir=None,
            out_nodata=255,
            admin_level: int = None,
    ):
        """
        Rasterize the sub-national admin boundaries (SubNational_reprojected.shp, produced by
        create_subnational_shapefile) onto the same grid as the reclassified cropland raster,
        then sample those values onto an existing GeoPackage and add an 'adm{admin_level}_name'
        column — mirrors how rasterize_farmlands rasterizes farmland polygons and samples them
        onto points to create the 'farms' column.

        admin_level (or the notebook's Calibration_method, read automatically if admin_level
        is not passed):
         - 0: nothing to do; prints a message and returns None.
         - 1 or 2: uses 'adm1_name' or 'adm2_name' respectively as the admin column read from
           admin_shapefile, and writes the result to a column named 'adm1_name' or
           'adm2_name' on the output points (named dynamically as f"adm{admin_level}_name").

        Parameters
        ----------
        Country_name : str
        reference_raster : str, optional
            Defaults to Data/01.BaseCroplandLayer/{Country_name}_reclassified_nod.tif.
        admin_shapefile : str, optional
            Defaults to Input_data/SubNational_reprojected.shp.
        target_vector : str, optional
            Defaults to Data/01.BaseCroplandLayer/{Country_name}.gpkg (the Step 1 points layer).
        output_dir : str, optional
            Defaults to Data/01.BaseCroplandLayer.
        out_nodata : int
            Value written where no admin polygon covers a pixel; points sampled there get a
            missing (NaN) adm{admin_level}_name.

        Writes:
         - {Country_name}_adm.tif   (int16 raster of admin codes; intermediate)
         - {Country_name}_adm.gpkg  (target_vector's points + 'adm{admin_level}_name')

        Returns the output GeoPackage path, or None if admin_level is 0.
        """
        if admin_level is None:
            frame = inspect.currentframe()
            try:
                caller = frame.f_back if frame is not None else None
                while caller is not None:
                    if "Calibration_method" in caller.f_locals:
                        admin_level = caller.f_locals["Calibration_method"]
                        break
                    if "Calibration_method" in caller.f_globals:
                        admin_level = caller.f_globals["Calibration_method"]
                        break
                    caller = caller.f_back
            finally:
                del frame
        admin_level = int(admin_level) if admin_level is not None else 0

        if admin_level <= 0:
            print("Calibration_method is 0 (adm0); no admin boundaries to rasterize, skipping.")
            return None

        output_dir = os.path.join(Data_dir, "01.BaseCroplandLayer") if output_dir is None else output_dir
        os.makedirs(output_dir, exist_ok=True)

        if reference_raster is None:
            reference_raster = os.path.join(output_dir, f"{Country_name}_reclassified_nod.tif")
        if admin_shapefile is None:
            admin_shapefile = os.path.join(Input_data, "SubNational_reprojected.shp")
        if target_vector is None:
            target_vector = os.path.join(output_dir, f"{Country_name}.gpkg")

        if not os.path.exists(reference_raster):
            raise FileNotFoundError(f"Reference raster not found: {reference_raster}")
        if not os.path.exists(admin_shapefile):
            raise FileNotFoundError(f"Admin boundary shapefile not found: {admin_shapefile}")
        if not os.path.exists(target_vector):
            raise FileNotFoundError(f"Target vector not found: {target_vector}")

        admin_col = "adm2_name" if admin_level > 1 else "adm1_name"

        with rasterio.open(reference_raster) as src:
            admin = gpd.read_file(admin_shapefile)
            if admin.crs != src.crs:
                admin = admin.to_crs(src.crs)

            if admin_col not in admin.columns:
                raise ValueError(f"'{admin_col}' column not found in {admin_shapefile}")

            # drop any exact duplicate rows before rasterizing
            dedup_cols = [c for c in ["adm2_name", "adm1_name", "area_sqkm"] if c in admin.columns]
            admin = admin.drop_duplicates(subset=dedup_cols).reset_index(drop=True)
            admin["admin_code"] = admin.index.astype(int) + 1  # avoid 0, reserved for out_nodata
            class_to_name = dict(zip(admin["admin_code"], admin[admin_col]))

            shapes = list(zip(admin.geometry, admin["admin_code"]))

            rasterized = rasterize(
                shapes,
                out_shape=(src.height, src.width),
                transform=src.transform,
                fill=out_nodata,
                dtype=np.int16,
            )

            raster_meta = src.meta.copy()
            raster_meta.update(
                driver="GTiff",
                dtype=rasterio.int16,
                count=1,
                nodata=out_nodata,
                compress="lzw",
            )

        admin_raster_fp = os.path.join(output_dir, f"{Country_name}_adm.tif")
        if os.path.exists(admin_raster_fp):
            os.remove(admin_raster_fp)
        with rasterio.open(admin_raster_fp, "w", **raster_meta) as dst:
            dst.write(rasterized, 1)
        print(f"Saved admin raster: {os.path.basename(admin_raster_fp)}")

        # read target vector and sample the admin raster at each point, same pattern as
        target_gdf = gpd.read_file(target_vector)
        with rasterio.open(admin_raster_fp) as src:
            if target_gdf.crs != src.crs:
                target_gdf = target_gdf.to_crs(src.crs)

            if target_gdf.geometry.geom_type.isin(["Point", "MultiPoint"]).all():
                sample_geoms = target_gdf.geometry
            else:
                sample_geoms = target_gdf.geometry.centroid
            coords = [(geom.x, geom.y) for geom in sample_geoms]

            sampled_vals = []
            for val in src.sample(coords):
                v = val[0]
                if v == src.nodata or v == out_nodata or pd.isna(v):
                    sampled_vals.append(None)
                else:
                    try:
                        sampled_vals.append(int(v))
                    except Exception:
                        sampled_vals.append(None)

        out_col = "adm"
        target_gdf["admin_code"] = sampled_vals
        target_gdf[out_col] = target_gdf["admin_code"].map(class_to_name)
        target_gdf = target_gdf.drop(columns=["admin_code"])

        missing_mask = target_gdf[out_col].isna()
        n_unmatched = int(missing_mask.sum())
        print(f"Points without a matched {admin_col}: {n_unmatched} out of {len(target_gdf)}")

        if n_unmatched > 0:
            # These points fall outside every admin polygon (e.g. a thin rim along the
            # country/coast edge where the admin boundaries don't quite reach the land
            # cover raster's extent) rather than in a sliver gap between two districts, so
            # a normal intersects-based join can't help. Assign each one the nearest
            # admin polygon instead, so every point ends up with a non-null adm.
            nearest = gpd.sjoin_nearest(
                target_gdf.loc[missing_mask, ["geometry"]],
                admin[[admin_col, "geometry"]],
                how="left",
                distance_col="_dist_to_admin",
            )
            nearest = nearest[~nearest.index.duplicated(keep="first")]
            target_gdf.loc[missing_mask, out_col] = nearest[admin_col].reindex(target_gdf.loc[missing_mask].index).values
            max_dist = nearest["_dist_to_admin"].max()
            print(f"Filled {n_unmatched} unmatched points with their nearest {admin_col} "
                  f"(max assignment distance: {max_dist:,.0f} m).")

        still_missing = int(target_gdf[out_col].isna().sum())
        if still_missing:
            raise ValueError(
                f"{still_missing} points still have no '{out_col}' after nearest-district "
                f"fallback — check that {admin_shapefile} has valid, non-empty geometries."
            )

        output_gpkg = os.path.join(output_dir, f"{Country_name}_adm.gpkg")
        if os.path.exists(output_gpkg):
            os.remove(output_gpkg)
        target_gdf.to_file(
            output_gpkg,
            driver="GPKG",
            layer=f"{Country_name}_adm",
            layer_options={"SPATIAL_INDEX": "NO"},
            engine="fiona",
        )

        print(f"Saved: {output_gpkg}")
        return output_gpkg

    def rasterize_farmlands(
        Country_name=str,
        reference_raster=None,
        farmland_shapefile=None,
        #class_lookup_csv=None,
        target_vector=None,
        output_dir=None,
        out_nodata=255,
        target_layer=None,
        admin_level: int = None,
    ):
        """
        Rasterize farmland polygons to the same grid as the reference land cover raster,
        then sample those values on an existing GeoPackage and add a "farms" column.

        Missing/no-farm pixels will have "NoFarm" in the 'farms' column.

        If target_vector is not given, it defaults to the Step 1 output points layer:
         - {Country_name}.gpkg if admin_level (or the notebook's Calibration_method, read
           automatically if admin_level is not passed) is 0.
         - {Country_name}_adm.gpkg if admin_level is 1 or 2 (the admin-tagged points layer
           produced by sampling {Country_name}_adm.tif from rasterize_admin_boundaries).
        """
        try:
            output_dir = os.path.join(Data_dir, "01.BaseCroplandLayer") if output_dir is None else output_dir
            os.makedirs(output_dir, exist_ok=True)

            if reference_raster is None:
                reference_raster = os.path.join(Input_data, f"{Country_name}_LandCover.tif")
            if farmland_shapefile is None:
                farmland_shapefile = os.path.join(Input_data, f"farmlands_reclassified.shp")
            if target_vector is None:
                if admin_level is None:
                    frame = inspect.currentframe()
                    try:
                        caller = frame.f_back if frame is not None else None
                        while caller is not None:
                            if "Calibration_method" in caller.f_locals:
                                admin_level = caller.f_locals["Calibration_method"]
                                break
                            if "Calibration_method" in caller.f_globals:
                                admin_level = caller.f_globals["Calibration_method"]
                                break
                            caller = caller.f_back
                    finally:
                        del frame
                admin_level = int(admin_level) if admin_level is not None else 0
                if admin_level > 0:
                    target_vector = os.path.join(output_dir, f"{Country_name}_adm.gpkg")
                else:
                    target_vector = os.path.join(output_dir, f"{Country_name}.gpkg")

            if not os.path.exists(reference_raster):
                raise FileNotFoundError(f"Reference raster not found: {reference_raster}")
            if not os.path.exists(farmland_shapefile):
                raise FileNotFoundError(f"Farmland shapefile not found: {farmland_shapefile}")
            if not os.path.exists(target_vector):
                raise FileNotFoundError(f"Target vector not found: {target_vector}")

            farmlands = gpd.read_file(farmland_shapefile)
            with rasterio.open(reference_raster) as src:
                if farmlands.crs != src.crs:
                    farmlands = farmlands.to_crs(src.crs)

                existing_ints = set()
                string_names = []
                for _, row in farmlands.iterrows():
                    v = row.get("crop_class", None)
                    if v is None or (isinstance(v, float) and pd.isna(v)):
                        continue
                    # detect numeric-like values
                    try:
                        iv = int(v)
                        existing_ints.add(int(iv))
                    except Exception:
                        s = str(v).strip()
                        if s not in string_names:
                            string_names.append(s)

                # assign integer codes for string names, avoiding collisions with existing ints and nodata
                start_code = 1
                if existing_ints:
                    start_code = max(existing_ints) + 1

                class_to_value = {}
                next_code = start_code
                for name in sorted(string_names, key=lambda x: x.lower()):
                    class_to_value[name] = next_code
                    next_code += 1

                # Build shapes list using either existing numeric code or assigned code
                shapes = []
                class_to_name = {}
                for _, row in farmlands.iterrows():
                    v = row.get("crop_class", None)
                    if v is None or (isinstance(v, float) and pd.isna(v)):
                        continue
                    try:
                        code = int(v)
                        shapes.append((row.geometry, code))
                        class_to_name[code] = str(v)
                    except Exception:
                        name = str(v).strip()
                        code = class_to_value.get(name)
                        if code is None:
                            continue
                        shapes.append((row.geometry, code))
                        class_to_name[code] = name

                if not shapes:
                    raise ValueError("No farmland polygons with valid crop_class values found")

                out_shape = (src.height, src.width)
                rasterized = rasterize(
                    shapes,
                    out_shape=out_shape,
                    transform=src.transform,
                    fill=out_nodata,
                    dtype=np.int16,
                )

                raster_meta = src.meta.copy()
                raster_meta.update(
                    driver="GTiff",
                    dtype=rasterio.int16,
                    count=1,
                    nodata=out_nodata,
                    compress="lzw",
                )

            farmland_raster_path = os.path.join(output_dir, f"{Country_name}_farmland.tif")
            if os.path.exists(farmland_raster_path):
                os.remove(farmland_raster_path)
            with rasterio.open(farmland_raster_path, "w", **raster_meta) as dst:
                dst.write(rasterized, 1)

            # read target vector and ensure same CRS
            target_gdf = gpd.read_file(target_vector, layer=target_layer) if target_layer else gpd.read_file(target_vector)
            if target_gdf.crs != src.crs:
                target_gdf = target_gdf.to_crs(src.crs)

            # sample using point geometries (centroid for polygons)
            if target_gdf.geometry.geom_type.isin(["Point", "MultiPoint"]).all():
                sample_geoms = target_gdf.geometry
            else:
                sample_geoms = target_gdf.geometry.centroid

            coords = [(geom.x, geom.y) for geom in sample_geoms]

            sampled_vals = []
            with rasterio.open(farmland_raster_path) as src:
                for val in src.sample(coords):
                    v = val[0]
                    # treat nodata as missing
                    if v == src.nodata or v == out_nodata or pd.isna(v):
                        sampled_vals.append(None)
                    else:
                        try:
                            sampled_vals.append(int(v))
                        except Exception:
                            sampled_vals.append(None)

            target_gdf["crop_class"] = sampled_vals
            # map to farmland names; missing -> "NoFarm"
            target_gdf["farms"] = target_gdf["crop_class"].map(class_to_name).where(target_gdf["crop_class"].notnull(), "NoFarm")
            # any unmapped numeric class -> NoFarm
            target_gdf["farms"] = target_gdf["farms"].fillna("NoFarm")

            # remove crop_class column before saving
            if "crop_class" in target_gdf.columns:
                target_gdf = target_gdf.drop(columns=["crop_class"])

            output_gpkg = os.path.join(output_dir, f"{Country_name}_crp_farmland.gpkg")
            # remove any existing (possibly corrupted) gpkg to force a clean create
            if os.path.exists(output_gpkg):
                os.remove(output_gpkg)

            try:
                # disable spatial index creation (avoids inserting into gpkg_extensions)
                target_gdf.to_file(
                    output_gpkg,
                    driver="GPKG",
                    layer=f"{Country_name}_crp_farmland",
                    layer_options={"SPATIAL_INDEX": "NO"},
                    engine="fiona",
                )
            except Exception:
                # fallback: write to temporary file then move into place
                tmp_fp = output_gpkg + ".tmp"
                if os.path.exists(tmp_fp):
                    os.remove(tmp_fp)
                target_gdf.to_file(
                    tmp_fp,
                    driver="GPKG",
                    layer=f"{Country_name}_crp_farmland",
                    layer_options={"SPATIAL_INDEX": "NO"},
                    engine="fiona",
                )
                os.replace(tmp_fp, output_gpkg)

            return farmland_raster_path, output_gpkg

        except Exception as e:
            error_msg = (
                f"\n{'='*80}\n"
                f"ERROR in rasterize_farmlands: {str(e)}\n"
                f"{'='*80}\n"
                f"⚠️  BEFORE RUNNING THIS CELL, RUN THE NOTEBOOK: FarmlandClassification.ipynb\n"
                f"\n"
                f"This function requires farmland data to be processed first.\n"
                f"Please complete the FarmlandClassification.ipynb notebook and ensure that\n"
                f"the required files (e.g., farmlands_reclassified.shp) are created\n"
                f"in the Data/00.InputData folder.\n"
                f"{'='*80}\n"
            )
            print(error_msg)
            raise
    
    def clip_rasters(
        Country_name= str,
        input_dir=None,
        boundary_path=None,
        output_dir=None,
        clip_nodata=0,
    ):
        """
        Clip rasters with country boundary and write clipped rasters
        into: Data/02.{Country_name}_MAPSPAM_Crops/01.{Country_name}_Clipped_Rasters/
        Keeps original filenames. Replaces all NoData placeholders (source nodata, NaN, 
        extreme float values) with clip_nodata (default 0).
        """
        output_root = os.path.join(Data_dir, f"02.{Country_name}_MAPSPAM_Crops") if output_dir is None else output_dir
        output_dir_clip = os.path.join(output_root, f"01.{Country_name}_Clipped_Rasters")
        os.makedirs(output_dir_clip, exist_ok=True)

        if input_dir is None:
            input_dir = os.path.join(Input_data, "spam2020V2r2_global_harvested_area")
        if boundary_path is None:
            boundary_path = os.path.join(Input_data, f"gadm41_{Country_name}_0.shp")

        if not os.path.isdir(input_dir):
            raise FileNotFoundError(f"MAPSPAM input folder not found: {input_dir}")
        if not os.path.exists(boundary_path):
            raise FileNotFoundError(f"Country boundary shapefile not found: {boundary_path}")

        boundary = gpd.read_file(boundary_path)
        if boundary.crs is None:
            raise ValueError(f"Boundary shapefile has no CRS: {boundary_path}")

        tif_files = sorted(glob.glob(os.path.join(input_dir, "*.tif")))
        clipped_rasters = []

        for input_fp in tif_files:
            name = os.path.basename(input_fp)
            try:
                with rasterio.open(input_fp) as src:
                    if src.crs is None:
                        raise ValueError(f"Raster has no CRS: {input_fp}")

                    # make boundary geometries in raster CRS
                    if src.crs != boundary.crs:
                        shapes = [geom for geom in boundary.to_crs(src.crs).geometry]
                    else:
                        shapes = [geom for geom in boundary.geometry]

                    # perform mask/clip
                    out_image, out_transform = mask(
                        src,
                        shapes=shapes,
                        crop=True,
                        nodata=clip_nodata,
                        filled=True,
                    )

                    # replace source nodata with clip_nodata
                    if src.nodata is not None:
                        out_image = np.where(out_image == src.nodata, clip_nodata, out_image)

                    # replace NaN and extreme float placeholders with clip_nodata
                    if np.issubdtype(out_image.dtype, np.floating):
                        out_image = np.where(np.isnan(out_image), clip_nodata, out_image)
                        out_image = np.where(np.abs(out_image) > 1e37, clip_nodata, out_image)

                    out_meta = src.meta.copy()
                    out_meta.update({
                        "driver": "GTiff",
                        "height": out_image.shape[1],
                        "width": out_image.shape[2],
                        "transform": out_transform,
                        "nodata": clip_nodata,
                        "compress": "lzw",
                    })

                output_fp = os.path.join(output_dir_clip, name)
                if os.path.exists(output_fp):
                    os.remove(output_fp)

                with rasterio.open(output_fp, "w", **out_meta) as dst:
                    dst.write(out_image)

                clipped_rasters.append(output_fp)
                print(f"  Clipped: {name}")

            except Exception as e:
                print(f"  Error clipping {name}: {e}")

        return output_dir_clip, clipped_rasters
    
    def reproject_rasters(
        Country_name=str,
        input_dir=str,
        output_dir=str,
        out_nodata=255,
        target_crs=None,
    ):
        """
        Reproject rasters to a target CRS without matching any reference grid.
        
        Args:
            Country_name: Country identifier
            input_dir: Path to input rasters directory
            output_dir: Output root directory
            out_nodata: NoData value for output rasters
            target_crs: Target CRS (pyproj.CRS, EPSG string, or int). If None, uses source CRS.
        
        Returns:
            reprojected_dir: Path to output reprojected rasters directory
            reprojected_files: List of created reprojected raster paths
        """
        # output_dir = output_dir or None

        # if output_dir is None:
        #     reprojected_dir = os.path.join(Data_dir, f"02.{Country_name}_MAPSPAM_Crops", "02.Reprojected_Rasters")
        #     default_input_dir = os.path.join(Data_dir, f"02.{Country_name}_MAPSPAM_Crops", f"01.{Country_name}_Clipped_Rasters")
        #     input_dir = input_dir or default_input_dir
        # else:
        #     reprojected_dir = output_dir
        #     input_dir = input_dir or os.path.join(output_dir, f"01.{Country_name}_Clipped_Rasters")

        # os.makedirs(reprojected_dir, exist_ok=True)

        reprojected_dir = output_dir
        os.makedirs(reprojected_dir, exist_ok=True)

        if not os.path.isdir(input_dir):
            raise FileNotFoundError(f"Input folder not found: {input_dir}")

        tif_files = sorted(glob.glob(os.path.join(input_dir, "*.tif")))
        if not tif_files:
            raise FileNotFoundError(f"No .tif files found in: {input_dir}")
        
        reprojected_files = []

        for input_fp in tif_files:
            name = os.path.basename(input_fp)
            try:
                with rasterio.open(input_fp) as src:
                    src_crs = src.crs
                    src_bounds = src.bounds

                    # Determine destination CRS
                    if target_crs is None:
                        dst_crs = src_crs
                    else:
                        dst_crs = rasterio.crs.CRS.from_user_input(target_crs)

                    # Calculate output transform and dimensions based on source bounds reprojected to target CRS
                    dst_transform, dst_width, dst_height = calculate_default_transform(
                        src_crs, dst_crs, src.width, src.height, *src_bounds
                    )

                    dst_meta = src.meta.copy()
                    dst_meta.update({
                        "driver": "GTiff",
                        "crs": dst_crs,
                        "transform": dst_transform,
                        "width": dst_width,
                        "height": dst_height,
                        "nodata": out_nodata,
                        "compress": "lzw",
                    })

                    destination = np.full((dst_height, dst_width), out_nodata, dtype=src.dtypes[0])

                    reproject(
                        source=rasterio.band(src, 1),
                        destination=destination,
                        src_transform=src.transform,
                        src_crs=src_crs,
                        dst_transform=dst_transform,
                        dst_crs=dst_crs,
                        resampling=Resampling.nearest,
                        src_nodata=src.nodata,
                        dst_nodata=out_nodata,
                    )

                    output_fp = os.path.join(reprojected_dir, name)
                    if os.path.exists(output_fp):
                        os.remove(output_fp)

                    with rasterio.open(output_fp, "w", **dst_meta) as dst:
                        dst.write(destination, 1)

                    reprojected_files.append(output_fp)
                    print(f"  Reprojected: {name}")

            except Exception as e:
                print(f"  Error reprojecting {name}: {e}")

        return reprojected_dir, reprojected_files

    def mapspam_filter_crops(
        Country_name=str,
        reprojected_dir=None,
        output_dir=None,
        summary_csv=None,
        out_nodata=255,
    ):
        output_root = os.path.join(Data_dir, f"02.{Country_name}_MAPSPAM_Crops") if output_dir is None else output_dir
        filtered_dir = os.path.join(output_root, f"03.{Country_name}_Filtered_Rasters")
        os.makedirs(filtered_dir, exist_ok=True)

        if reprojected_dir is None:
            reprojected_dir = os.path.join(output_root, "02.Reprojected_Rasters")
        if summary_csv is None:
            summary_csv = os.path.join(output_root, "MAPSPAMsummary_stat.csv")

        if not os.path.isdir(reprojected_dir):
            raise FileNotFoundError(f"Reprojected raster folder not found: {reprojected_dir}")

        pattern = re.compile(r"spam2020_V2r2_global_H_([A-Z]{4})_([AIR])\.tif$", re.IGNORECASE)
        crop_groups = {}

        for input_fp in sorted(glob.glob(os.path.join(reprojected_dir, "*.tif"))):
            match = pattern.match(os.path.basename(input_fp))
            if not match:
                continue
            crop_code, crop_type = match.groups()
            crop_groups.setdefault(crop_code, {})[crop_type.upper()] = input_fp

        rows = []
        for crop_code, files in crop_groups.items():
            a_fp = files.get("A")
            i_fp = files.get("I")
            r_fp = files.get("R")

            total_A = 0.0
            total_I = 0.0
            total_R = 0.0

            if a_fp:
                with rasterio.open(a_fp) as src_a:
                    a_data = src_a.read(1, masked=True)
                    total_A = float(np.sum(a_data.filled(0.0)))

            if i_fp:
                with rasterio.open(i_fp) as src_i:
                    i_data = src_i.read(1, masked=True)
                    total_I = float(np.sum(i_data.filled(0.0)))

            if r_fp:
                with rasterio.open(r_fp) as src_r:
                    r_data = src_r.read(1, masked=True)
                    total_R = float(np.sum(r_data.filled(0.0)))

            share_i = float(total_I / total_A) if total_A > 0 else 0.0
            if share_i >= 0.95:
                share_i = 1.0
            elif share_i < 0.05:
                share_i = 0.0
            else:
                share_i = round(share_i, 2)

            rows.append({
                "crop": crop_code,
                "I": total_I,
                "R": total_R,
                "A": total_A,
                "Share_I": share_i,
            })

            # Only copy I raster if total_I > 0 - preserve CRS by using rasterio
            if total_I > 0 and i_fp:
                with rasterio.open(i_fp) as src:
                    meta = src.meta.copy()
                    output_i_fp = os.path.join(filtered_dir, os.path.basename(i_fp))
                    if os.path.exists(output_i_fp):
                        os.remove(output_i_fp)
                    with rasterio.open(output_i_fp, "w", **meta) as dst:
                        dst.write(src.read())

            # Only copy R raster if total_R > 0 - preserve CRS by using rasterio
            if total_R > 0 and r_fp:
                with rasterio.open(r_fp) as src:
                    meta = src.meta.copy()
                    output_r_fp = os.path.join(filtered_dir, os.path.basename(r_fp))
                    if os.path.exists(output_r_fp):
                        os.remove(output_r_fp)
                    with rasterio.open(output_r_fp, "w", **meta) as dst:
                        dst.write(src.read())

        df = pd.DataFrame(rows, columns=["crop", "I", "R", "A", "Share_I"])
        df.to_csv(summary_csv, index=False)

        return filtered_dir, summary_csv, df


    def calculate_density_rasters(
        Country_name=str,
        filtered_dir=None,
        output_dir=None,
        out_nodata=255,
    ):
        """
        Calculate density rasters from filtered MAPSPAM rasters.
        Density = Harvested Area (pixel value) / Pixel Area (in hectares)
        
        The pixel area is calculated based on the raster's coordinate system.
        
        Args:
            Country_name: Country identifier
            filtered_dir: Path to filtered rasters directory
            output_dir: Output root directory
            out_nodata: NoData value for output rasters
        
        Returns:
            density_dir: Path to output density rasters directory
            density_files: List of created density raster paths
        """
        output_root = os.path.join(Data_dir, f"02.{Country_name}_MAPSPAM_Crops") if output_dir is None else output_dir
        density_dir = os.path.join(output_root, "04.Density_Rasters")
        os.makedirs(density_dir, exist_ok=True)

        if filtered_dir is None:
            filtered_dir = os.path.join(output_root, f"03.{Country_name}_Filtered_Rasters")

        if not os.path.isdir(filtered_dir):
            raise FileNotFoundError(f"Filtered rasters folder not found: {filtered_dir}")

        tif_files = sorted(glob.glob(os.path.join(filtered_dir, "*.tif")))
        density_files = []

        for input_fp in tif_files:
            name = os.path.basename(input_fp)
            try:
                with rasterio.open(input_fp) as src:
                    data = src.read(1, masked=True)
                    transform = src.transform

                    # Calculate pixel area in hectares
                    # Pixel dimensions from the transform
                    pixel_width = abs(transform.a)
                    pixel_height = abs(transform.e)
                    
                    # Assume CRS units are in meters (as per land cover raster)
                    pixel_area_m2 = pixel_width * pixel_height
                    pixel_area_ha = pixel_area_m2 / 10000

                    # Calculate density: harvested area / pixel area
                    # Replace masked/nodata values with 0 for calculation
                    data_filled = data.filled(0.0)
                    density_data = data_filled / pixel_area_ha

                    # Preserve original nodata pattern
                    density_data = np.ma.masked_where(data.mask, density_data)
                    
                    # Update metadata - PRESERVE CRS and other properties
                    dst_meta = src.meta.copy()
                    dst_meta.update({
                        "driver": "GTiff",
                        "dtype": rasterio.float32,
                        "nodata": out_nodata,
                        "compress": "lzw",
                        "crs": src.crs,  # Explicitly preserve CRS
                        "transform": src.transform,  # Preserve geotransform
                    })

                    output_fp = os.path.join(density_dir, name)
                    if os.path.exists(output_fp):
                        os.remove(output_fp)

                    with rasterio.open(output_fp, "w", **dst_meta) as dst:
                        # Convert masked array to regular array with nodata values
                        output_data = density_data.filled(out_nodata).astype(np.float32)
                        dst.write(output_data, 1)

                    density_files.append(output_fp)
                    print(f"  Density raster created: {name}")

            except Exception as e:
                print(f"  Error processing {name}: {e}")

        return density_dir, density_files

class Map:
    def __init__(self):
        pass

        """
        This class contains all the methods needed for plotting the maps used for the analysis. 
        The functions in this class are used to run step 6-8. 
        These functions return the maps of the case-study country, with the farmlands and the filtered MAPSPAM rasters. 

        """   
    def resample_raster_to_points(
        Country_name=str,
        input_dir=None,
        cropland_gpkg=None,
        output_dir=None,
        out_nodata=None,
    ):
        
        """
        This function creates a GeoPackage with point geometries corresponding to the cropland locations
        by resampling rasters to the base cropland point layer. 
        """ 
        if input_dir is None:
            input_dir = os.path.join(Data_dir, f"02.{Country_name}_MAPSPAM_Crops", "04.Density_Rasters")

        if cropland_gpkg is None:
            cropland_gpkg = os.path.join(Data_dir, "01.BaseCroplandLayer", f"{Country_name}_crp_farmland.gpkg")

        if output_dir is None:
            output_dir = os.path.join(Data_dir, "03.Final", f"01.{Country_name}_crop_IR")
        os.makedirs(output_dir, exist_ok=True)

        out_gpkg = os.path.join(output_dir, f"{Country_name}_crop_IR.gpkg")

        if out_nodata is None: 
            out_nodata = 255

        if not os.path.isdir(input_dir):
            raise FileNotFoundError(f"Input raster folder not found: {input_dir}")
        if not os.path.exists(cropland_gpkg):
            raise FileNotFoundError(f"Cropland point layer not found: {cropland_gpkg}")

        gdf = gpd.read_file(cropland_gpkg)
        if gdf.crs is None:
            raise ValueError("Cropland layer has no CRS")

        raster_files = sorted(glob.glob(os.path.join(input_dir, "*.tif")))
        if not raster_files:
            raise FileNotFoundError(f"No density rasters found in: {input_dir}")

        # Ensure points are in raster CRS
        with rasterio.open(raster_files[0]) as src0:
            raster_crs = src0.crs
            if raster_crs is None:
                raise ValueError(f"First raster has no CRS: {raster_files[0]}")
            if not gdf.crs.is_exact_same(raster_crs):
                gdf = gdf.to_crs(raster_crs)

        # Pattern to extract crop code (4 uppercase letters) and system (I/R/A)
        pattern = re.compile(r"([A-Z]{4})_([AIR])\.tif$", re.IGNORECASE)

        for raster_path in raster_files:
            basename = os.path.basename(raster_path)
            match = pattern.search(basename)
            if not match:
                continue
            crop_code, system = match.groups()
            field_name = f"{crop_code.upper()}_{system.upper()}"

            with rasterio.open(raster_path) as src:
                nod = src.nodata if src.nodata is not None else out_nodata
                data = src.read(1)
                transform = src.transform
                
                vals = []
                for pt in gdf.geometry:
                    # Convert point coords to pixel row/col
                    row, col = rasterio.transform.rowcol(transform, pt.x, pt.y)
                    
                    # Check bounds
                    if 0 <= row < src.height and 0 <= col < src.width:
                        pixel_val = data[row, col]
                    else:
                        pixel_val = nod
                    
                    # Replace nodata with 0.0
                    if pixel_val is None or (isinstance(pixel_val, float) and np.isnan(pixel_val)) or (nod is not None and pixel_val == nod):
                        vals.append(0.0)
                    else:
                        vals.append(float(pixel_val))
                
            gdf[field_name] = np.array(vals, dtype=np.float32)

        if os.path.exists(out_gpkg):
            os.remove(out_gpkg)

        gdf.to_file(
            out_gpkg,
            driver="GPKG",
            layer=f"{Country_name}_crop_IR",
            layer_options={"SPATIAL_INDEX": "NO"},
            engine="fiona",
        )

        print(f"Saved resampled point vector: {os.path.basename(out_gpkg)} with {len(gdf)} points")
        return output_dir, out_gpkg, gdf

    def calculate_admin_mapspam_summary(
        Country_name: str,
        input_gpkg: str = None,
        landcover_tif: str = None,
        out_dir: str = None,
        summary_csv: str = None,
        admin_level: int = None,
    ):
        """
        Computes a MAPSPAM summary (I, R, A, Share_I per crop) per admin unit, from the
        fine-resolution resampled cropland points (input_gpkg, default {Country_name}_crop_IR.gpkg)
        rather than from the raw ~10km MAPSPAM rasters used by Raster.mapspam_filter_crops. By
        this point density has already been resampled onto the fine land-cover grid, so this
        avoids the small-district-smaller-than-one-MAPSPAM-pixel zonal-stat problem.

        admin_level (or the notebook's Calibration_method, read automatically if admin_level is
        not passed):
         - 0: nothing to do (there is no admin column yet); prints a message and returns None.
         - 1 or 2: input_gpkg must already contain an 'adm' column (adm1_name or adm2_name,
           matching whichever level rasterize_admin_boundaries/rasterize_farmlands were run
           with). Rows are grouped by 'adm', and for every crop column ({CODE}_I / {CODE}_R):
            I = sum(density_I) * point_area_ha
            R = sum(density_R) * point_area_ha
            A = I + R
            Share_I = I/A, snapped to 1.0 if >=0.99, to 0.0 if <0.01, else rounded to 2 dp
           (same rule as Raster.mapspam_filter_crops).

        Writes (default Data/03.Final/02.Calibration/MAPSPAMsummary_stat_adm{admin_level}.csv):
            columns [adm, crop, I, R, A, Share_I]

        Returns (summary_csv_path, DataFrame), or None if admin_level is 0.
        """
        if admin_level is None:
            frame = inspect.currentframe()
            try:
                caller = frame.f_back if frame is not None else None
                while caller is not None:
                    if "Calibration_method" in caller.f_locals:
                        admin_level = caller.f_locals["Calibration_method"]
                        break
                    if "Calibration_method" in caller.f_globals:
                        admin_level = caller.f_globals["Calibration_method"]
                        break
                    caller = caller.f_back
            finally:
                del frame
        admin_level = int(admin_level) if admin_level is not None else 0

        if admin_level <= 0:
            print("Calibration_method is 0 (adm0); no admin-level MAPSPAM summary to compute.")
            return None

        if input_gpkg is None:
            input_gpkg = os.path.join(
                Data_dir, "03.Final", f"01.{Country_name}_crop_IR", f"{Country_name}_crop_IR.gpkg",
            )
        if landcover_tif is None:
            landcover_tif = os.path.join(Input_data, f"{Country_name}_LandCover.tif")
        if out_dir is None:
            out_dir = os.path.join(Data_dir, "03.Final", "02.Calibration")
        os.makedirs(out_dir, exist_ok=True)
        if summary_csv is None:
            summary_csv = os.path.join(out_dir, f"MAPSPAMsummary_stat_adm{admin_level}.csv")

        if not os.path.exists(input_gpkg):
            raise FileNotFoundError(f"Input gpkg not found: {input_gpkg}")
        gdf = gpd.read_file(input_gpkg)

        if "adm" not in gdf.columns:
            raise ValueError(
                f"'adm' column not found in {input_gpkg}. Calibration_method is set to "
                f"{admin_level}, so the cropland points must already carry an 'adm' column "
                f"(added upstream when the admin boundaries were sampled onto the points) "
                f"before this summary can be computed."
            )

        if not os.path.exists(landcover_tif):
            raise FileNotFoundError(f"Landcover raster not found: {landcover_tif}")
        with rasterio.open(landcover_tif) as lc:
            t = lc.transform
            pixel_area_m2 = abs(t.a * t.e)
            point_area_ha = pixel_area_m2 / 10000.0

        crop_cols = [c for c in gdf.columns if isinstance(c, str) and re.match(r"^[A-Z]{4}_[IR]$", c)]
        if not crop_cols:
            raise ValueError("No MAPSPAM crop columns found in input gpkg")

        records_I = []
        records_R = []
        for col in crop_cols:
            crop = col.split("_")[0].upper()
            system = col.split("_")[1].upper()
            vals = pd.to_numeric(gdf[col].fillna(0), errors="coerce").fillna(0.0).astype(float) * point_area_ha
            df = pd.DataFrame({"adm": gdf["adm"], "crop": crop, "value": vals})
            (records_I if system == "I" else records_R).append(df)

        def _group(records):
            if not records:
                return pd.DataFrame(columns=["adm", "crop", "value"])
            long_df = pd.concat(records, ignore_index=True)
            return long_df.groupby(["adm", "crop"], dropna=False, as_index=False)["value"].sum()

        sum_I = _group(records_I).rename(columns={"value": "I"})
        sum_R = _group(records_R).rename(columns={"value": "R"})

        summary = sum_I.merge(sum_R, on=["adm", "crop"], how="outer")
        summary["I"] = summary["I"].fillna(0.0)
        summary["R"] = summary["R"].fillna(0.0)
        summary["A"] = summary["I"] + summary["R"]

        def _share_i(i_val, a_val):
            share_i = float(i_val / a_val) if a_val > 0 else 0.0
            if share_i >= 0.99:
                return 1.0
            elif share_i < 0.01:
                return 0.0
            return round(share_i, 2)

        summary["Share_I"] = summary.apply(lambda r: _share_i(r["I"], r["A"]), axis=1)
        summary = summary.sort_values(["adm", "crop"]).reset_index(drop=True)
        summary = summary[["adm", "crop", "I", "R", "A", "Share_I"]]

        summary.to_csv(summary_csv, index=False)
        print(f"Saved admin-level MAPSPAM summary: {summary_csv}")

        return summary_csv, summary

    def filter_empty_crop_points(
        Country_name=str,
        input_gpkg=None,
        output_dir=None,
    ):
        if input_gpkg is None:
            input_gpkg = os.path.join(
                Data_dir,
                "03.Final",
                f"01.{Country_name}_crop_IR",
                f"{Country_name}_crop_IR.gpkg",
            )

        if output_dir is None:
            output_dir = os.path.join(Data_dir, "03.Final", f"01.{Country_name}_crop_IR")
        os.makedirs(output_dir, exist_ok=True)

        output_fp = os.path.join(output_dir, f"{Country_name}_crop_IR_filtered.gpkg")

        if not os.path.exists(input_gpkg):
            raise FileNotFoundError(f"Input GeoPackage not found: {input_gpkg}")

        gdf = gpd.read_file(input_gpkg)
        if "farms" not in gdf.columns:
            raise ValueError("Input layer has no 'farms' column")

        cols = list(gdf.columns)
        farms_idx = cols.index("farms")

        # columns to test are those after "farms", excluding geometry and x/y fields
        cols_to_check = [
            c
            for c in cols[farms_idx + 1 :]
            if c not in {"geometry", "x", "y"}
        ]
        if not cols_to_check:
            raise ValueError("No crop columns found after 'farms'")

        values = gdf[cols_to_check].fillna(0)
        if not np.issubdtype(values.dtypes[0], np.number):
            # ensure all checked columns are numeric
            values = values.apply(pd.to_numeric, errors="coerce").fillna(0)

        mask = (gdf["farms"] == "NoFarm") & (values == 0).all(axis=1)
        filtered_gdf = gdf.loc[~mask].copy()

        if os.path.exists(output_fp):
            os.remove(output_fp)
        filtered_gdf.to_file(
            output_fp,
            driver="GPKG",
            layer=f"{Country_name}_crop_IR_filtered",
            layer_options={"SPATIAL_INDEX": "NO"},
            engine="fiona",
        )

        return output_fp, filtered_gdf

    def calculate_pixel_sum(Country_name: str, input_gpkg: str, out_csv: str = None):
        """
        Sum all columns with crops for each pixel in the GeoPackage.

        Writes:
            - pixel_sum.csv
        Returns:
            - output CSV path in the 03.Final folder
        """
        if out_csv is None:
            out_dir = os.path.join(Data_dir, "03.Final", "02.Calibration")
            os.makedirs(out_dir, exist_ok=True)
            safe_base = os.path.splitext(os.path.basename(input_gpkg))[0]
            out_csv = os.path.join(out_dir, f"{safe_base}_pixel_sum.csv")
        else:
            os.makedirs(os.path.dirname(out_csv), exist_ok=True)
            out_dir = os.path.dirname(out_csv)

        if not os.path.exists(input_gpkg):
            raise FileNotFoundError(f"Input GeoPackage not found: {input_gpkg}")

        gdf = gpd.read_file(input_gpkg)
        if "farms" not in gdf.columns:
            raise ValueError("The GeoPackage must contain a 'farms' column.")

        cols = list(gdf.columns)
        farms_index = cols.index("farms")
        sum_cols = [
            c
            for c in cols[farms_index + 1 :]
            if c not in {"geometry", "x", "y"}
        ]

        if not sum_cols:
            raise ValueError("No crop columns found after the 'farms' column.")

        numeric = gdf[sum_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)
        pixel_sum = numeric.sum(axis=1)

        if "pixel_id" in gdf.columns:
            ids = gdf["pixel_id"].astype(str)
        elif "id" in gdf.columns:
            ids = gdf["id"].astype(str)
        else:
            ids = pd.Series(gdf.index.astype(str), index=gdf.index)

        out_df = pd.DataFrame({"pixel_id": ids, "Sum": pixel_sum})
        out_df.to_csv(out_csv, index=False)

        # Check rows > 1
        rows_gt1 = out_df[out_df["Sum"] > 1.0000001] #allow tolerance
        safe_name = os.path.splitext(os.path.basename(input_gpkg))[0]
        check_csv = os.path.join(out_dir, f"CheckRows_{safe_name}.csv")

        if not rows_gt1.empty:
            rows_gt1.to_csv(check_csv, index=False)
            return f"For rows >1 check the file: {check_csv} in the folder {out_dir}"
        else:
            return "No rows with sum > 1"

    def mapspam_total_harvested_area(
            Country_name: str,
            input_gpkg: str = None,
            landcover_tif: str = None,
            out_dir: str = None,
            admin_level: int = None,
    ):
        """
        This function calculates the total harvested area per crop from the created crop_IT_filtered.gpkg.
        The function multiplies crop's density by the point area (corresponding to the landcover pixel area) to get the harvested are (in hectares) per point.

        admin_level (or the notebook's Calibration_method, read automatically if admin_level
        is not passed):
         - 0: national totals per crop (unchanged).
         - 1 or 2: input_gpkg must already contain an 'adm' column. Totals are grouped by
           'adm' AND crop instead of crop alone, so each output row is one (adm, crop) pair.

        Saves in out_dir (default Data/03.Final/02.Calibration):
         - Sum_MAPSPAM_Irrigated.csv
         - Sum_MAPSPAM_Rainfed.csv
           columns [crop, MAPSPAM_HA] when admin_level is 0, or [adm, crop, MAPSPAM_HA]
           when admin_level is 1 or 2.

        Returns a message string with the output path(s).
        """

        if landcover_tif is None:
            landcover_tif = os.path.join(Input_data, f"{Country_name}_LandCover.tif")
        if out_dir is None:
            out_dir = os.path.join(Data_dir, "03.Final", "02.Calibration")
        os.makedirs(out_dir, exist_ok=True)

        if admin_level is None:
            frame = inspect.currentframe()
            try:
                caller = frame.f_back if frame is not None else None
                while caller is not None:
                    if "Calibration_method" in caller.f_locals:
                        admin_level = caller.f_locals["Calibration_method"]
                        break
                    if "Calibration_method" in caller.f_globals:
                        admin_level = caller.f_globals["Calibration_method"]
                        break
                    caller = caller.f_back
            finally:
                del frame
        admin_level = int(admin_level) if admin_level is not None else 0

        # read filtered points gpkg
        if not os.path.exists(input_gpkg):
            raise FileNotFoundError(f"Input gpkg not found: {input_gpkg}")
        gdf = gpd.read_file(input_gpkg)

        if admin_level > 0 and "adm" not in gdf.columns:
            raise ValueError(
                f"'adm' column not found in {input_gpkg}. Calibration_method is set to "
                f"{admin_level}, so the cropland points must already carry an 'adm' column "
                f"before this total can be computed."
            )

        # compute point pixel area (ha) from landcover raster transform
        if not os.path.exists(landcover_tif):
            raise FileNotFoundError(f"Landcover raster not found: {landcover_tif}")
        with rasterio.open(landcover_tif) as lc:
            t = lc.transform #transform maps pixel coordinates (row,col) to geographic coordinates (x,y).
            pixel_area_m2 = abs(t.a * t.e) #t.a is the width of a pixel and t.e is the height.
            point_area_ha = pixel_area_m2 / 10000.0

        # identify MAPSPAM crop columns (format: XXXX_I or XXXX_R)
        crop_cols = [c for c in gdf.columns if isinstance(c, str) and re.match(r"^[A-Z]{4}_[IR]$", c)]
        if not crop_cols:
            raise ValueError("No MAPSPAM crop columns found in filtered gpkg")

        # Sum MAPSPAM area per crop/system (and per admin unit if admin_level > 0):
        # Area = density * point_area_ha
        records_I = []
        records_R = []
        for col in crop_cols:
            crop = col.split("_")[0].upper()
            system = col.split("_")[1].upper()
            vals = pd.to_numeric(gdf[col].fillna(0), errors="coerce").fillna(0.0).astype(float) * point_area_ha
            row = {"crop": crop, "MAPSPAM_HA": vals}
            if admin_level > 0:
                row["adm"] = gdf["adm"]
            df = pd.DataFrame(row)
            (records_I if system == "I" else records_R).append(df)

        group_cols = ["crop"] if admin_level <= 0 else ["adm", "crop"]

        def _summarize(records):
            if not records:
                return pd.DataFrame(columns=group_cols + ["MAPSPAM_HA"])
            long_df = pd.concat(records, ignore_index=True)
            # dropna=False keeps a row for any point that didn't match an admin polygon,
            # so unmatched harvested area is visible instead of silently dropped
            summary = long_df.groupby(group_cols, dropna=False, as_index=False)["MAPSPAM_HA"].sum()
            return summary.sort_values(group_cols).reset_index(drop=True)

        sumI_df = _summarize(records_I)
        sumR_df = _summarize(records_R)

        sumI_fp = os.path.join(out_dir, "Sum_MAPSPAM_Irrigated.csv")
        sumR_fp = os.path.join(out_dir, "Sum_MAPSPAM_Rainfed.csv")

        sumI_df.to_csv(sumI_fp, index=False)
        sumR_df.to_csv(sumR_fp, index=False)

        return (f"Total MAPSPAM harvested area per irrigated and rainfed crop saved to:\n  {sumI_fp}\n  {sumR_fp}\n")    

  
    
    def calculate_national_statistics(
            Country_name: str,
            cropnames_csv: str = None,
            harvested_csv: str = None,
            out_dir: str = None,
            faostat_country_name: str = None,
            **kwargs,
    ):

        """
        This function calculates the total harvested area per crop estimated from national statistics.
        It reads the FAOSTAT-wide harvested-area table by filtering for the requested country and
        the crop names defined in CropNames.csv. These data are needed for the final calibration.

        Saves in out_dir (default Data/03.Final/02.Calibration):
         - National_Harvested_{Country_name}.csv
         - National_irrigated_crops_{Country_name}.csv
         - National_rainfed_crops_{Country_name}.csv
        """
        if cropnames_csv is None:
            cropnames_csv = os.path.join(Input_data, "CropNames.csv")
        if harvested_csv is None:
            harvested_csv = os.path.join(Input_data, "FAOSTAT_HarvestedArea2023.csv")
        if out_dir is None:
            out_dir = os.path.join(Data_dir, "03.Final", "02.Calibration")
        os.makedirs(out_dir, exist_ok=True)

        if not os.path.exists(cropnames_csv):
            raise FileNotFoundError(f"Crop names CSV not found: {cropnames_csv}")
        if not os.path.exists(harvested_csv):
            raise FileNotFoundError(f"Harvested area CSV not found: {harvested_csv}")

        if faostat_country_name is None:
            faostat_country_name = kwargs.pop("FAOSTAT_country_name", None)
        if faostat_country_name is None:
            frame = inspect.currentframe()
            try:
                caller = frame.f_back if frame is not None else None
                while caller is not None:
                    if "FAOSTAT_country_name" in caller.f_locals:
                        faostat_country_name = caller.f_locals["FAOSTAT_country_name"]
                        break
                    if "FAOSTAT_country_name" in caller.f_globals:
                        faostat_country_name = caller.f_globals["FAOSTAT_country_name"]
                        break
                    caller = caller.f_back
            finally:
                del frame

        if faostat_country_name is None:
            raise ValueError(
                "FAOSTAT country name was not provided. Define FAOSTAT_country_name in the User Settings."
            )
        faostat_country_name = str(faostat_country_name).strip()
        if not faostat_country_name:
            raise ValueError("FAOSTAT country name cannot be empty.")

        crop_map = pd.read_csv(cropnames_csv, dtype=str).fillna("")
        if {"Crop", "MAPSPAM"}.issubset(set(crop_map.columns)):
            mapping = crop_map[["Crop", "MAPSPAM"]].copy()
        else:
            mapping = crop_map.iloc[:, :2].copy()
            mapping.columns = ["Crop", "MAPSPAM"]

        mapping["Crop"] = mapping["Crop"].astype(str).str.strip()
        mapping["MAPSPAM"] = mapping["MAPSPAM"].astype(str).str.upper().str.strip()

        def _normalize_crop_name(crop_name):
            return re.sub(r"[^0-9a-z]+", "", str(crop_name).strip().lower())

        mapping["Crop_norm"] = mapping["Crop"].map(_normalize_crop_name)

        harvested = pd.read_csv(harvested_csv, dtype=str).fillna("")

        def _normalize_col_name(col_name):
            return str(col_name).strip().lower().replace(" ", "")

        cols_lookup = {_normalize_col_name(c): c for c in harvested.columns}
        area_col = cols_lookup.get("area")
        item_col = cols_lookup.get("item")
        value_col = cols_lookup.get("value")

        if area_col is None or item_col is None or value_col is None:
            raise ValueError(
                f"Harvested CSV must contain 'Area', 'Item' and 'Value' columns: {harvested_csv}"
            )

        country_mask = harvested[area_col].astype(str).str.strip().str.casefold() == faostat_country_name.casefold()
        if not country_mask.any():
            raise ValueError(
                f"Country '{faostat_country_name}' was not found in the FAOSTAT harvest file '{harvested_csv}'. Please check the FAOSTAT_country_name input."
            )

        harvested = harvested.loc[country_mask, [item_col, value_col]].copy()
        harvested.columns = ["Crop", "Area"]
        harvested["Crop"] = harvested["Crop"].astype(str).str.strip()
        harvested["Crop_norm"] = harvested["Crop"].map(_normalize_crop_name)
        harvested["Area"] = pd.to_numeric(
            harvested["Area"].astype(str).str.replace(",", ""),
            errors="coerce",
        ).fillna(0.0)

        merged = harvested.merge(mapping[["Crop_norm", "MAPSPAM"]], on="Crop_norm", how="left")
        if merged["MAPSPAM"].isna().any():
            missing = merged.loc[merged["MAPSPAM"].isna(), "Crop"].unique()
            if len(missing) > 0:
                print(f"Warning: {len(missing)} national crop names were not mapped to MAPSPAM codes. First values: {missing[:10].tolist()}")

        merged["MAPSPAM"] = merged["MAPSPAM"].fillna("UNKNOWN")
        national_sum = (
            merged.groupby("MAPSPAM", dropna=False)["Area"]
            .sum()
            .reset_index()
            .rename(columns={"MAPSPAM": "MAPSPAM_Crop", "Area": "National_Area (ha)"})
        )

        output_fp = os.path.join(out_dir, f"National_harvested_{Country_name}.csv")
        national_sum.to_csv(output_fp, index=False)

        # read MAPSPAM summary to get Share_I and compute national irrigated/rainfed splits
        mapspam_summary_csv = os.path.join(
            Data_dir, f"02.{Country_name}_MAPSPAM_Crops", "MAPSPAMsummary_stat.csv"
        )
        irrigated_fp = os.path.join(out_dir, f"National_irrigated_crops_{Country_name}.csv")
        rainfed_fp = os.path.join(out_dir, f"National_rainfed_crops_{Country_name}.csv")

        # prepare empty templates in case we cannot compute them
        empty_df = pd.DataFrame(columns=["MAPSPAM_Crop", "National_Area (ha)"])

        if not os.path.exists(mapspam_summary_csv):
            print(f"MAPSPAM summary not found: {mapspam_summary_csv}; creating empty irrigated/rainfed outputs.")
            empty_df.to_csv(irrigated_fp, index=False)
            empty_df.to_csv(rainfed_fp, index=False)
            return output_fp

        # try reading MAPSPAM summary
        try:
            msum = pd.read_csv(mapspam_summary_csv, dtype=str).fillna("0")
        except Exception as e:
            print(f"Warning: could not read MAPSPAM summary CSV: {e}; creating empty irrigated/rainfed outputs.")
            empty_df.to_csv(irrigated_fp, index=False)
            empty_df.to_csv(rainfed_fp, index=False)
            return output_fp

        # normalize column names (case-insensitive)
        cols_lower = {c.lower(): c for c in msum.columns}
        if "crop" in cols_lower and "share_i" in cols_lower:
            msum = msum.rename(columns={cols_lower["crop"]: "crop", cols_lower["share_i"]: "Share_I"})

        if "crop" not in msum.columns or "Share_I" not in msum.columns:
            print(f"Warning: MAPSPAM summary missing required columns 'crop' and/or 'Share_I': {mapspam_summary_csv}; creating empty irrigated/rainfed outputs.")
            empty_df.to_csv(irrigated_fp, index=False)
            empty_df.to_csv(rainfed_fp, index=False)
            return output_fp

        msum["crop"] = msum["crop"].astype(str).str.upper().str.strip()
        msum["Share_I"] = pd.to_numeric(msum["Share_I"].astype(str), errors="coerce").fillna(0.0)

        # join national_sum (MAPSPAM_Crop) with msum (crop) keeping only crops present in both
        joined = national_sum.merge(msum[["crop", "Share_I"]], left_on="MAPSPAM_Crop", right_on="crop", how="inner")

        if joined.empty:
            print(f"No overlapping MAPSPAM crops between {output_fp} and {mapspam_summary_csv}; creating empty irrigated/rainfed outputs.")
            empty_df.to_csv(irrigated_fp, index=False)
            empty_df.to_csv(rainfed_fp, index=False)
            return output_fp

        # compute irrigated and rainfed national areas for the intersection
        joined["Irrigated_ha"] = joined["National_Area (ha)"].astype(float) * joined["Share_I"].astype(float)
        irr_df = joined[["MAPSPAM_Crop", "Irrigated_ha"]].copy()
        irr_df = irr_df.rename(columns={"Irrigated_ha": "National_Area (ha)"})
        irr_df.to_csv(irrigated_fp, index=False)

        joined["Rainfed_ha"] = (joined["National_Area (ha)"].astype(float) - joined["Irrigated_ha"]).clip(lower=0.0)
        rain_df = joined[["MAPSPAM_Crop", "Rainfed_ha"]].copy()
        rain_df = rain_df.rename(columns={"Rainfed_ha": "National_Area (ha)"})
        rain_df.to_csv(rainfed_fp, index=False)

        return output_fp



    def calculate_scaling_factor(Country_name: str, admin_level: int = None):


        """
        This function calculates the scaling factor for each crop and system (irrigated/rainfed)
        by dividing the national harvested area by the MAPSPAM harvested area.

        admin_level (or the notebook's Calibration_method, read automatically if admin_level
        is not passed):
         - 0: one national scaling factor per crop (unchanged). Reads:
            - National_irrigated_crops_{Country_name}.csv
            - National_rainfed_crops_{Country_name}.csv
            - Sum_MAPSPAM_Irrigated.csv
            - Sum_MAPSPAM_Rainfed.csv
         - 1 or 2: Input_data/adm{admin_level}_NationalStat.csv is read first. For every crop
           that appears as a column in that file, that district's combined Target_HA is split
           into irrigated/rainfed using that crop's adm-AND-crop-specific Share_I from
           MAPSPAMsummary_stat_adm{admin_level}.csv (produced by calculate_admin_mapspam_summary),
           then each system gets its own scaling factor, Target_system_HA / MAPSPAM_system_HA,
           for that (adm, crop) — so scaling_factor_irrigated and scaling_factor_rainfed
           genuinely differ, unlike before.
           If MAPSPAM_system_HA is 0 for that (adm, crop, system) — no existing pixel to
           scale — a multiplicative factor could never place that target area (factor x 0 is
           always 0). That row is instead marked Method="inject" with Inject_HA set to the
           target area, so calibrate_crops knows to ADD that area, spread evenly across the
           district's points, rather than multiply. Every other row is Method="scale".
           Any crop NOT covered by adm{admin_level}_NationalStat.csv instead uses the exact
           same method as admin_level=0 (National_irrigated/rainfed_crops_{Country_name}.csv
           vs. the nationally-summed MAPSPAM totals), with the resulting single national
           factor per system replicated across every adm unit (always Method="scale").

        Writes:
         - scaling_factor_irrigated.csv
         - scaling_factor_rainfed.csv
           columns [Crop, Scaling_factor] when admin_level is 0, or
           [adm, crop, Scaling_factor, Method, Inject_HA] when admin_level is 1 or 2.
        """
        out_dir = os.path.join(Data_dir, "03.Final", "02.Calibration")
        os.makedirs(out_dir, exist_ok=True)

        if admin_level is None:
            frame = inspect.currentframe()
            try:
                caller = frame.f_back if frame is not None else None
                while caller is not None:
                    if "Calibration_method" in caller.f_locals:
                        admin_level = caller.f_locals["Calibration_method"]
                        break
                    if "Calibration_method" in caller.f_globals:
                        admin_level = caller.f_globals["Calibration_method"]
                        break
                    caller = caller.f_back
            finally:
                del frame
        admin_level = int(admin_level) if admin_level is not None else 0

        mapspam_i_fp = os.path.join(out_dir, "Sum_MAPSPAM_Irrigated.csv")
        mapspam_r_fp = os.path.join(out_dir, "Sum_MAPSPAM_Rainfed.csv")
        if not os.path.exists(mapspam_i_fp):
            raise FileNotFoundError(f"MAPSPAM irrigated summary CSV not found: {mapspam_i_fp}")
        if not os.path.exists(mapspam_r_fp):
            raise FileNotFoundError(f"MAPSPAM rainfed summary CSV not found: {mapspam_r_fp}")

        national_irr_fp = os.path.join(out_dir, f"National_irrigated_crops_{Country_name}.csv")
        national_rain_fp = os.path.join(out_dir, f"National_rainfed_crops_{Country_name}.csv")
        if not os.path.exists(national_irr_fp):
            raise FileNotFoundError(f"National irrigated CSV not found: {national_irr_fp}")
        if not os.path.exists(national_rain_fp):
            raise FileNotFoundError(f"National rainfed CSV not found: {national_rain_fp}")

        def safe_scale(target_area, mapspam_area, crop, system):
            target_area = float(target_area)
            mapspam_area = float(mapspam_area)
            if target_area <= 0.0:
                return 0.0
            if mapspam_area <= 0.0:
                print(f"Warning: MAPSPAM {system} area is zero or missing for crop '{crop}'; setting scaling factor to 1.0")
                return 1.0
            return target_area / mapspam_area

        def _normalize_crop_area(df, crop_names, area_names):
            cols = {c.lower(): c for c in df.columns}
            crop_col = next((cols[k] for k in crop_names if k in cols), None)
            area_col = next((cols[k] for k in area_names if k in cols), None)
            if crop_col is None or area_col is None:
                raise ValueError(f"Could not find expected crop/area columns in CSV: {df.columns.tolist()}")
            df = df[[crop_col, area_col]].copy()
            df.columns = ["crop", "area"]
            df["crop"] = df["crop"].astype(str).str.upper().str.strip()
            df["area"] = pd.to_numeric(df["area"].astype(str).str.replace(",", ""), errors="coerce").fillna(0.0)
            return df

        def _build_scaling_df(national_df, mapspam_df, system):
            df = national_df.merge(mapspam_df, on="crop", how="left")
            df["mapspam_area"] = df["area_y"].fillna(0.0)
            df["national_area"] = df["area_x"].fillna(0.0)
            df["Scaling_factor"] = df.apply(
                lambda row: safe_scale(row["national_area"], row["mapspam_area"], row["crop"], system),
                axis=1,
            )
            out = df[["crop", "Scaling_factor"]].copy()
            out["Crop"] = out["crop"]
            return out[["Crop", "Scaling_factor"]]

        national_irr = _normalize_crop_area(
            pd.read_csv(national_irr_fp, dtype=str).fillna(""),
            crop_names=["mapspam_crop", "crop", "MAPSPAM_Crop", "MAPSPAM_CROP"],
            area_names=["national_area (ha)", "national_area_ha", "national_area", "area"],
        )
        national_rain = _normalize_crop_area(
            pd.read_csv(national_rain_fp, dtype=str).fillna(""),
            crop_names=["mapspam_crop", "crop", "MAPSPAM_Crop", "MAPSPAM_CROP"],
            area_names=["national_area (ha)", "national_area_ha", "national_area", "area"],
        )

        scaling_i_fp = os.path.join(out_dir, "scaling_factor_irrigated.csv")
        scaling_r_fp = os.path.join(out_dir, "scaling_factor_rainfed.csv")

        if admin_level <= 0:
            mapspam_i = _normalize_crop_area(
                pd.read_csv(mapspam_i_fp, dtype=str).fillna(""),
                crop_names=["crop"],
                area_names=["mapspam_ha", "area", "value"],
            )
            mapspam_r = _normalize_crop_area(
                pd.read_csv(mapspam_r_fp, dtype=str).fillna(""),
                crop_names=["crop"],
                area_names=["mapspam_ha", "area", "value"],
            )

            scaling_i_df = _build_scaling_df(national_irr, mapspam_i, "irrigated")
            scaling_r_df = _build_scaling_df(national_rain, mapspam_r, "rainfed")

            scaling_i_df.to_csv(scaling_i_fp, index=False)
            scaling_r_df.to_csv(scaling_r_fp, index=False)

            return scaling_i_fp, scaling_r_fp

        # --- admin_level 1 or 2 ---

        district_csv = os.path.join(Input_data, f"adm{admin_level}_NationalStat.csv")
        if not os.path.exists(district_csv):
            raise FileNotFoundError(
                f"Calibration_method is set to {admin_level} but "
                f"'{os.path.basename(district_csv)}' was not found in {Input_data}. Check that "
                f"Calibration_method matches an existing adm{{level}}_NationalStat.csv file."
            )

        admin_id_col = f"adm{admin_level}_name"
        dist_df = pd.read_csv(district_csv, dtype=str)
        if admin_id_col not in dist_df.columns:
            raise ValueError(f"'{admin_id_col}' column not found in {district_csv}")

        crop_cols_in_district = [
            c for c in dist_df.columns if re.match(r"^[A-Za-z]{4}$", str(c).strip())
        ]
        district_crops = set(c.strip().upper() for c in crop_cols_in_district)

        long_rows = []
        for _, row in dist_df.iterrows():
            adm_val = str(row[admin_id_col]).strip()
            for c in crop_cols_in_district:
                val = pd.to_numeric(str(row[c]).replace(",", ""), errors="coerce")
                long_rows.append((adm_val, c.strip().upper(), 0.0 if pd.isna(val) else float(val)))
        target_long = pd.DataFrame(long_rows, columns=["adm", "crop", "Target_HA"])

        def _read_admin_mapspam(path):
            df = pd.read_csv(path, dtype=str).fillna("")
            cols = {c.lower(): c for c in df.columns}
            adm_col = cols.get("adm")
            crop_col = cols.get("crop")
            area_col = next((cols[k] for k in ["mapspam_ha", "area", "value"] if k in cols), None)
            if adm_col is None or crop_col is None or area_col is None:
                raise ValueError(
                    f"Expected columns 'adm', 'crop', 'MAPSPAM_HA' in {path}; found {df.columns.tolist()}. "
                    f"Re-run mapspam_total_harvested_area with admin_level={admin_level} first."
                )
            out = df[[adm_col, crop_col, area_col]].copy()
            out.columns = ["adm", "crop", "area"]
            out["adm"] = out["adm"].astype(str).str.strip()
            out["crop"] = out["crop"].astype(str).str.upper().str.strip()
            out["area"] = pd.to_numeric(out["area"].astype(str).str.replace(",", ""), errors="coerce").fillna(0.0)
            return out

        mapspam_i_adm = _read_admin_mapspam(mapspam_i_fp)
        mapspam_r_adm = _read_admin_mapspam(mapspam_r_fp)

        # adm-AND-crop-specific irrigated share, from calculate_admin_mapspam_summary's output
        admin_summary_fp = os.path.join(out_dir, f"MAPSPAMsummary_stat_adm{admin_level}.csv")
        if not os.path.exists(admin_summary_fp):
            raise FileNotFoundError(
                f"{admin_summary_fp} not found. Run Map.calculate_admin_mapspam_summary first — "
                f"its Share_I per (adm, crop) is needed to split {os.path.basename(district_csv)}'s "
                f"combined harvested area into irrigated/rainfed."
            )
        admin_summary = pd.read_csv(admin_summary_fp, dtype=str).fillna("")
        admin_summary["adm"] = admin_summary["adm"].astype(str).str.strip()
        admin_summary["crop"] = admin_summary["crop"].astype(str).str.upper().str.strip()
        admin_summary["Share_I"] = pd.to_numeric(admin_summary["Share_I"], errors="coerce").fillna(0.0)

        # crops covered by adm{admin_level}_NationalStat.csv: split Target_HA by adm+crop Share_I,
        # then one scaling factor per system for that (adm, crop)
        district_rows = target_long[target_long["crop"].isin(district_crops)].merge(
            admin_summary[["adm", "crop", "Share_I"]], on=["adm", "crop"], how="left"
        )
        district_rows["Share_I"] = district_rows["Share_I"].fillna(0.0)
        district_rows["Target_I_HA"] = district_rows["Target_HA"] * district_rows["Share_I"]
        district_rows["Target_R_HA"] = district_rows["Target_HA"] - district_rows["Target_I_HA"]

        district_rows = district_rows.merge(
            mapspam_i_adm.rename(columns={"area": "MAPSPAM_I_HA"}), on=["adm", "crop"], how="left"
        ).merge(
            mapspam_r_adm.rename(columns={"area": "MAPSPAM_R_HA"}), on=["adm", "crop"], how="left"
        )
        district_rows["MAPSPAM_I_HA"] = district_rows["MAPSPAM_I_HA"].fillna(0.0)
        district_rows["MAPSPAM_R_HA"] = district_rows["MAPSPAM_R_HA"].fillna(0.0)

        def _scale_or_inject(target_area, mapspam_area, crop, system):
            """
            Like safe_scale, but when MAPSPAM has zero presence for this (adm, crop, system)
            there is no pixel to multiply, so a multiplicative factor can never place the
            district's target area (factor x 0 = 0 regardless of the factor). Instead this
            flags the row for calibrate_crops to ADD the target area directly, spread evenly
            across the district's points, rather than scale existing density.
            Returns (scaling_factor, method, inject_ha).
            """
            target_area = float(target_area)
            mapspam_area = float(mapspam_area)
            if target_area <= 0.0:
                return 0.0, "scale", 0.0
            if mapspam_area <= 0.0:
                print(
                    f"Warning: MAPSPAM {system} area is zero for crop '{crop}'; the district "
                    f"target ({target_area:.2f} ha) will be added evenly across the district's "
                    f"points instead of scaled."
                )
                return 0.0, "inject", target_area
            return target_area / mapspam_area, "scale", 0.0

        district_rows[["Scaling_factor_I", "Method_I", "Inject_HA_I"]] = district_rows.apply(
            lambda r: pd.Series(_scale_or_inject(r["Target_I_HA"], r["MAPSPAM_I_HA"], r["crop"], "district-irrigated")),
            axis=1,
        )
        district_rows[["Scaling_factor_R", "Method_R", "Inject_HA_R"]] = district_rows.apply(
            lambda r: pd.Series(_scale_or_inject(r["Target_R_HA"], r["MAPSPAM_R_HA"], r["crop"], "district-rainfed")),
            axis=1,
        )

        district_factors_i = district_rows[["adm", "crop"]].assign(
            Scaling_factor=district_rows["Scaling_factor_I"],
            Method=district_rows["Method_I"],
            Inject_HA=district_rows["Inject_HA_I"],
        )
        district_factors_r = district_rows[["adm", "crop"]].assign(
            Scaling_factor=district_rows["Scaling_factor_R"],
            Method=district_rows["Method_R"],
            Inject_HA=district_rows["Inject_HA_R"],
        )

        # crops NOT covered by the district file: exact same method as admin_level=0,
        # replicated across every adm unit
        missing_crops = sorted((set(national_irr["crop"]) | set(national_rain["crop"])) - district_crops)
        if missing_crops:
            print(
                f"{len(missing_crops)} crop(s) not covered by {os.path.basename(district_csv)}; "
                f"falling back to the national (admin_level=0) scaling method for: {missing_crops}"
            )

        mapspam_i_national = mapspam_i_adm.groupby("crop", as_index=False)["area"].sum()
        mapspam_r_national = mapspam_r_adm.groupby("crop", as_index=False)["area"].sum()

        scaling_i_national = _build_scaling_df(national_irr, mapspam_i_national, "irrigated")
        scaling_r_national = _build_scaling_df(national_rain, mapspam_r_national, "rainfed")

        all_adm = sorted(target_long["adm"].unique())

        def _fallback_expanded(scaling_national_df):
            fallback = scaling_national_df[scaling_national_df["Crop"].isin(missing_crops)].rename(columns={"Crop": "crop"})
            fallback = fallback.assign(Method="scale", Inject_HA=0.0)
            return fallback.merge(pd.DataFrame({"adm": all_adm}), how="cross")[
                ["adm", "crop", "Scaling_factor", "Method", "Inject_HA"]
            ]

        fallback_i = _fallback_expanded(scaling_i_national)
        fallback_r = _fallback_expanded(scaling_r_national)

        scaling_i_df = pd.concat([district_factors_i, fallback_i], ignore_index=True).sort_values(["adm", "crop"]).reset_index(drop=True)
        scaling_r_df = pd.concat([district_factors_r, fallback_r], ignore_index=True).sort_values(["adm", "crop"]).reset_index(drop=True)

        scaling_i_df.to_csv(scaling_i_fp, index=False)
        scaling_r_df.to_csv(scaling_r_fp, index=False)

        return scaling_i_fp, scaling_r_fp

    def calibrate_crops(
        Country_name: str,
        input_gpkg: str = None,
        scaling_i_fp: str = None,
        scaling_r_fp: str = None,
        output_dir: str = None,
        admin_level: int = None,
        landcover_tif: str = None,
    ):
        """
        Calibrate crop density points using irrigated and rainfed scaling factors.

        admin_level (or the notebook's Calibration_method, read automatically if admin_level
        is not passed):
         - 0: scaling_i_fp/scaling_r_fp have columns [Crop, Scaling_factor] — one factor per
           crop, applied uniformly to every row (unchanged). Any crop column whose factor is
           missing or <= 0 is dropped entirely.
         - 1 or 2: scaling_i_fp/scaling_r_fp instead have columns [adm, crop, Scaling_factor,
           Method, Inject_HA] (from calculate_scaling_factor) — one row per admin unit AND
           crop. input_gpkg must already have an 'adm' column.
            - Method == "scale": the row is multiplied by Scaling_factor, as before, matched
              on the point's own 'adm' value (so the same crop can be scaled differently in
              different districts). A row whose (adm, crop) has no matching factor is treated
              as factor 0.
            - Method == "inject": MAPSPAM had zero presence for this (adm, crop, system), so
              there is no density to multiply (factor x 0 is always 0). Instead, Inject_HA
              (the district's target area, in hectares) is converted to a density and ADDED
              equally to every point of that district: Inject_HA / (n_points_in_district x
              point_area_ha). This is a sum, not a multiplication, so it can place area even
              where the existing column value is exactly 0.

        Writes a calibrated output GeoPackage named
        f"{Country_name}_crop_IR_calibrated1.gpkg".
        """

        if input_gpkg is None:
            input_gpkg = os.path.join(
                Data_dir,
                "03.Final",
                f"01.{Country_name}_crop_IR",
                f"{Country_name}_crop_IR.gpkg",
            )
        if scaling_i_fp is None:
            scaling_i_fp = os.path.join(
                Data_dir,
                "03.Final",
                "02.Calibration",
                "scaling_factor_irrigated.csv",
            )
        if scaling_r_fp is None:
            scaling_r_fp = os.path.join(
                Data_dir,
                "03.Final",
                "02.Calibration",
                "scaling_factor_rainfed.csv",
            )
        if output_dir is None:
            output_dir = os.path.join(Data_dir, "03.Final", f"01.{Country_name}_crop_IR")
        os.makedirs(output_dir, exist_ok=True)

        if not os.path.exists(input_gpkg):
            raise FileNotFoundError(f"Input GeoPackage not found: {input_gpkg}")
        if not os.path.exists(scaling_i_fp):
            raise FileNotFoundError(f"Irrigated scaling CSV not found: {scaling_i_fp}")
        if not os.path.exists(scaling_r_fp):
            raise FileNotFoundError(f"Rainfed scaling CSV not found: {scaling_r_fp}")

        if admin_level is None:
            frame = inspect.currentframe()
            try:
                caller = frame.f_back if frame is not None else None
                while caller is not None:
                    if "Calibration_method" in caller.f_locals:
                        admin_level = caller.f_locals["Calibration_method"]
                        break
                    if "Calibration_method" in caller.f_globals:
                        admin_level = caller.f_globals["Calibration_method"]
                        break
                    caller = caller.f_back
            finally:
                del frame
        admin_level = int(admin_level) if admin_level is not None else 0

        gdf = gpd.read_file(input_gpkg)
        if "farms" not in gdf.columns:
            raise ValueError("The GeoPackage must contain a 'farms' column.")

        cols = list(gdf.columns)
        farms_index = cols.index("farms")
        crop_cols = [
            c
            for c in cols[farms_index + 1 :]
            if isinstance(c, str) and re.match(r"^[A-Za-z]{4}_[IRir]$", c)
        ]
        if not crop_cols:
            raise ValueError("No crop columns found after the 'farms' column.")

        if admin_level <= 0:
            def _read_scaling_csv(path):
                df = pd.read_csv(path, dtype=str).fillna("")
                cols_lower = {c.lower(): c for c in df.columns}
                crop_col = next((cols_lower[k] for k in ["crop"] if k in cols_lower), None)
                factor_col = next(
                    (cols_lower[k] for k in ["scaling_factor", "scalingfactor", "factor", "value"] if k in cols_lower),
                    None,
                )
                if crop_col is None or factor_col is None:
                    raise ValueError(
                        f"Scaling factor CSV {path} must contain columns named 'Crop' and 'Scaling_factor'"
                    )
                df = df[[crop_col, factor_col]].copy()
                df.columns = ["crop", "scaling_factor"]
                df["crop"] = df["crop"].astype(str).str.upper().str.strip()
                df["scaling_factor"] = pd.to_numeric(
                    df["scaling_factor"].astype(str).str.replace(",", ""),
                    errors="coerce",
                ).fillna(0.0)
                return dict(zip(df["crop"], df["scaling_factor"]))

            irrigated_factors = _read_scaling_csv(scaling_i_fp)
            rainfed_factors = _read_scaling_csv(scaling_r_fp)

            drop_cols = []
            for col in crop_cols:
                crop, system = col.rsplit("_", 1)
                crop = crop.upper().strip()
                system = system.upper().strip()
                if system == "I":
                    factor = irrigated_factors.get(crop, 0.0)
                else:
                    factor = rainfed_factors.get(crop, 0.0)

                if factor <= 0.0:
                    drop_cols.append(col)
                    continue

                gdf[col] = pd.to_numeric(gdf[col].fillna(0), errors="coerce").fillna(0.0) * float(factor)

            if drop_cols:
                gdf = gdf.drop(columns=drop_cols)

        else:
            if "adm" not in gdf.columns:
                raise ValueError(
                    f"'adm' column not found in {input_gpkg}. Calibration_method is set to "
                    f"{admin_level}, so the cropland points must already carry an 'adm' column "
                    f"before per-district calibration can be applied."
                )

            if landcover_tif is None:
                landcover_tif = os.path.join(Input_data, f"{Country_name}_LandCover.tif")
            if not os.path.exists(landcover_tif):
                raise FileNotFoundError(f"Landcover raster not found: {landcover_tif}")
            with rasterio.open(landcover_tif) as lc:
                t = lc.transform
                pixel_area_m2 = abs(t.a * t.e)
                point_area_ha = pixel_area_m2 / 10000.0

            adm_values = gdf["adm"].astype(str).str.strip()
            n_points_per_adm = adm_values.value_counts()

            def _read_admin_scaling_csv(path):
                df = pd.read_csv(path, dtype=str).fillna("")
                cols_lower = {c.lower(): c for c in df.columns}
                adm_col = cols_lower.get("adm")
                crop_col = cols_lower.get("crop")
                factor_col = next(
                    (cols_lower[k] for k in ["scaling_factor", "scalingfactor", "factor", "value"] if k in cols_lower),
                    None,
                )
                if adm_col is None or crop_col is None or factor_col is None:
                    raise ValueError(
                        f"Scaling factor CSV {path} must contain columns 'adm', 'crop' and "
                        f"'Scaling_factor' when Calibration_method > 0; found {df.columns.tolist()}"
                    )
                method_col = cols_lower.get("method")
                inject_col = cols_lower.get("inject_ha")

                df["adm"] = df[adm_col].astype(str).str.strip()
                df["crop"] = df[crop_col].astype(str).str.upper().str.strip()
                df["scaling_factor"] = pd.to_numeric(
                    df[factor_col].astype(str).str.replace(",", ""),
                    errors="coerce",
                ).fillna(0.0)
                df["method"] = (
                    df[method_col].astype(str).str.strip().str.lower().replace("", "scale")
                    if method_col
                    else "scale"
                )
                df["inject_ha"] = (
                    pd.to_numeric(df[inject_col].astype(str).str.replace(",", ""), errors="coerce").fillna(0.0)
                    if inject_col
                    else 0.0
                )

                # nested dict: {crop: {adm: (factor, method, inject_ha)}} for a fast per-crop lookup
                nested = {}
                for crop, sub in df.groupby("crop"):
                    nested[crop] = {
                        row.adm: (row.scaling_factor, row.method, row.inject_ha)
                        for row in sub.itertuples()
                    }
                return nested

            irrigated_factors = _read_admin_scaling_csv(scaling_i_fp)
            rainfed_factors = _read_admin_scaling_csv(scaling_r_fp)

            for col in crop_cols:
                crop, system = col.rsplit("_", 1)
                crop = crop.upper().strip()
                system = system.upper().strip()
                factors_lookup = irrigated_factors if system == "I" else rainfed_factors
                crop_factor_map = factors_lookup.get(crop, {})

#This part is where the code injects the target area for crops that have no existing MAPSPAM presence 
                factor_map = {}
                additive_map = {}
                for adm_val, (factor, method, inject_ha) in crop_factor_map.items():
                    if method == "inject":
                        n_points = n_points_per_adm.get(adm_val, 0) # number of cropland points per district
                        additive = (
                            inject_ha / (n_points * point_area_ha)
                            if n_points > 0 and point_area_ha > 0
                            else 0.0
                        )
                        factor_map[adm_val] = 1.0  # keep whatever (near-zero) density already exists
                        additive_map[adm_val] = additive
                    else:
                        factor_map[adm_val] = factor
                        additive_map[adm_val] = 0.0

                row_factors = adm_values.map(factor_map).fillna(0.0).astype(float)
                row_additive = adm_values.map(additive_map).fillna(0.0).astype(float)
                gdf[col] = (
                    pd.to_numeric(gdf[col].fillna(0), errors="coerce").fillna(0.0) * row_factors
                    + row_additive
                )

        output_fp = os.path.join(output_dir, f"{Country_name}_crop_IR_calibrated1.gpkg")
        if os.path.exists(output_fp):
            os.remove(output_fp)

        gdf.to_file(
            output_fp,
            driver="GPKG",
            layer=f"{Country_name}_crop_IR_calibrated1",
            layer_options={"SPATIAL_INDEX": "NO"},
            engine="fiona",
        )

        return output_fp

    def redistribute_crop_density(
        Country_name: str,
        input_gpkg: str = None,
        output_dir: str = None,
        out_gpkg_name: str = None,
        target_crs=None,
        tolerance: float = 1e-6,
        admin_level: int = None,
        landcover_tif: str = None,
    ):
        """
        Redistribute crop density so every point has total density <= 1.0.

        Two-phase redistribution, using a KD-tree (scipy.spatial.cKDTree) per group instead
        of a brute-force nearest-neighbor search over every point, which is what made this
        step slow before:
        1) Prioritise the distribution of irrigated crops: rows where sum(_I) > 1 are
           redistributed to the nearest point(s) with irrigated capacity.
        2) Finalise by redistributing only rainfed values so each row total <= 1.

        admin_level (or the notebook's Calibration_method, read automatically if admin_level
        is not passed):
         - 0: both phases search nationwide (one KD-tree over every point) — same scope as
           before, just faster.
         - 1 or 2: input_gpkg must have an 'adm' column. Both phases are run independently
           PER ADMIN UNIT — a separate KD-tree is built per adm, and excess density can only
           be redistributed to another point in the SAME adm, never across district/province
           borders. This keeps the per-district calibration from Map.calibrate_crops from
           being undone by redistribution leaking mass elsewhere, and is also much faster
           than a national search, since each group is a small fraction of the whole dataset.
           If a specific admin unit's own total demand (summed across every crop and both
           systems) exceeds how many cropland pixels it has, Phase 2 cannot place all of it
           within that admin unit no matter how it's rearranged. Rather than raising an error,
           it caps that admin unit's rainfed total to whatever capacity remains after
           irrigated is protected (irrigated is never reduced here — "irrigation is always
           prioritised"): every rainfed crop column in that admin unit is multiplied by the
           SAME single scale factor, so each crop's share of the admin unit's rainfed total
           (as reported in the sub-national statistics) is preserved exactly, only the overall
           level is brought down to fit. Every (adm, crop) this happens to is logged to
           Data/03.Final/02.Calibration/redistribution_capacity_shortfall.csv, columns
           [adm, crop, Original_Rainfed_HA, Scale_Factor, Final_Rainfed_HA, Unallocated_HA].
           The file is always written (empty but with headers if nothing needed capping).

        Returns output path.
        """
        print("Seat back and relax, this may take a while for large datasets...")

        if input_gpkg is None:
            input_gpkg = os.path.join(
                Data_dir,
                "03.Final",
                f"01.{Country_name}_crop_IR",
                f"{Country_name}_crop_IR_calibrated1.gpkg",
            )

        if output_dir is None:
            output_dir = os.path.join(os.path.dirname(input_gpkg), "")
        os.makedirs(output_dir, exist_ok=True)
        if out_gpkg_name is None:
            out_gpkg_name = f"{Country_name}_crop_IR_calibrated2.gpkg"
        output_fp = os.path.join(output_dir, out_gpkg_name)

        if admin_level is None:
            frame = inspect.currentframe()
            try:
                caller = frame.f_back if frame is not None else None
                while caller is not None:
                    if "Calibration_method" in caller.f_locals:
                        admin_level = caller.f_locals["Calibration_method"]
                        break
                    if "Calibration_method" in caller.f_globals:
                        admin_level = caller.f_globals["Calibration_method"]
                        break
                    caller = caller.f_back
            finally:
                del frame
        admin_level = int(admin_level) if admin_level is not None else 0

        gdf = gpd.read_file(input_gpkg)
        if target_crs is not None:
            gdf = gdf.to_crs(target_crs)
        elif gdf.crs is None:
            raise ValueError("The input GeoPackage has no CRS. Provide target_crs to assign or enforce a projection.")

        cols = list(gdf.columns)
        if "farms" not in cols:
            raise ValueError("The GeoPackage must contain a 'farms' column.")
        farms_index = cols.index("farms")
        crop_cols = [
            c for c in cols[farms_index + 1 :] if isinstance(c, str) and re.match(r"^[A-Z]{4}_[IR]$", c)
        ]
        if not crop_cols:
            raise ValueError("No crop columns found after the 'farms' column.")

        if admin_level > 0 and "adm" not in gdf.columns:
            raise ValueError(
                f"'adm' column not found in {input_gpkg}. Calibration_method is set to "
                f"{admin_level}, so the cropland points must already carry an 'adm' column "
                f"before per-district redistribution can be applied."
            )

        if admin_level > 0:
            if landcover_tif is None:
                landcover_tif = os.path.join(Input_data, f"{Country_name}_LandCover.tif")
            if not os.path.exists(landcover_tif):
                raise FileNotFoundError(f"Landcover raster not found: {landcover_tif}")
            with rasterio.open(landcover_tif) as lc:
                t = lc.transform
                point_area_ha = abs(t.a * t.e) / 10000.0
            shortfall_records = []

        rainfed_cols = [c for c in crop_cols if c.endswith("_R")]
        irrigated_cols = [c for c in crop_cols if c.endswith("_I")]

        # numeric matrices (one row per point, one column per crop/system)
        values_R = gdf[rainfed_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0).astype(float).to_numpy() if rainfed_cols else np.zeros((len(gdf), 0), dtype=float)
        values_I = gdf[irrigated_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0).astype(float).to_numpy() if irrigated_cols else np.zeros((len(gdf), 0), dtype=float)

        coords = np.array([[geom.x, geom.y] for geom in gdf.geometry])
        if coords.size == 0:
            raise ValueError("Input GeoPackage contains no geometries.")

        tol = 1e-8
        max_iters = 10000

        # groups to redistribute independently: one per adm unit, or a single national group
        if admin_level > 0:
            adm_values = gdf["adm"].astype(str).str.strip().to_numpy()
            group_labels = sorted(set(adm_values))
            group_indices = {g: np.where(adm_values == g)[0] for g in group_labels}
        else:
            group_labels = ["the whole country"]
            group_indices = {group_labels[0]: np.arange(len(gdf))}

        def _expanding_knn_receivers(tree, coords_local, capacity_local, src_local, k_start=16):
            """
            Yields local indices (within one group) in roughly nearest-first order, limited
            to points with spare capacity, by querying the KD-tree with an expanding k instead
            of sorting distances to every point in the group up front.
            """
            n_local = len(coords_local)
            if n_local <= 1:
                return
            seen = np.zeros(n_local, dtype=bool)
            seen[src_local] = True
            k = min(k_start, n_local)
            while True:
                _, idxs = tree.query(coords_local[src_local], k=k)
                idxs = np.atleast_1d(idxs)
                for j in idxs:
                    j = int(j)
                    if seen[j]:
                        continue
                    seen[j] = True
                    if capacity_local[j] > tol:
                        yield j
                if k >= n_local:
                    return
                k = min(k * 4, n_local)

        # ------------------------------------------------------------------
        # Phase 1: redistribute irrigated-only so no row has sum(I) > 1, per group
        # ------------------------------------------------------------------
        for group_label in group_labels:
            idx = group_indices[group_label]
            if idx.size == 0:
                continue
            coords_local = coords[idx]
            values_I_local = values_I[idx].copy()
            sum_I_local = values_I_local.sum(axis=1) if values_I_local.size else np.zeros(idx.size)
            tree = cKDTree(coords_local) if idx.size > 1 else None

            it = 0
            while True:
                over = np.where(sum_I_local > 1.0 + tol)[0]
                if len(over) == 0:
                    break
                if it > max_iters:
                    raise RuntimeError(f"Phase1: max iters reached in {group_label}")
                src = over[np.argmax(sum_I_local[over] - 1.0)]
                excess = sum_I_local[src] - 1.0
                if excess <= tol:
                    sum_I_local[src] = min(sum_I_local[src], 1.0)
                    it += 1
                    continue

                src_comp = values_I_local[src].copy()
                total_src_comp = src_comp.sum()
                if total_src_comp <= tol:
                    if sum_I_local[src] > 1.0:
                        values_I_local[src] *= (1.0 / sum_I_local[src])
                        sum_I_local[src] = values_I_local[src].sum()
                    it += 1
                    continue

                if tree is not None:
                    capacity_local = np.maximum(1.0 - sum_I_local, 0.0)
                    for t in _expanding_knn_receivers(tree, coords_local, capacity_local, src):
                        cap = max(0.0, 1.0 - sum_I_local[t])
                        if cap <= tol:
                            continue
                        transfer = min(excess, cap)
                        frac = src_comp / total_src_comp
                        move = frac * transfer
                        values_I_local[t] += move
                        values_I_local[src] -= move
                        sum_I_local[t] = values_I_local[t].sum()
                        sum_I_local[src] = values_I_local[src].sum()
                        excess = sum_I_local[src] - 1.0
                        if sum_I_local[src] <= 1.0 + tol:
                            break

                # if no receiver in this group accepted enough flow, scale source down to 1.0
                if values_I_local[src].sum() > 1.0 + tol:
                    scale = 1.0 / values_I_local[src].sum()
                    values_I_local[src] *= scale
                    sum_I_local[src] = values_I_local[src].sum()

                it += 1

            values_I[idx] = values_I_local

        # write irrigated back to gdf (rainfed columns unchanged in Phase 1)
        for i, c in enumerate(irrigated_cols):
            gdf[c] = values_I[:, i]

        # ------------------------------------------------------------------
        # Phase 2: redistribute only rainfed to satisfy total<=1, per group
        # ------------------------------------------------------------------
        for group_label in group_labels:
            idx = group_indices[group_label]
            if idx.size == 0:
                continue
            coords_local = coords[idx]
            values_I_local = values_I[idx].copy()
            values_R_local = values_R[idx].copy()
            tree = cKDTree(coords_local) if idx.size > 1 else None

            sum_I_local = np.sum(values_I_local, axis=1) if values_I_local.size else np.zeros(idx.size)
            total_local = sum_I_local + (np.sum(values_R_local, axis=1) if values_R_local.size else 0.0)
            capacity_local = np.maximum(1.0 - total_local, 0.0)

            # If this admin unit's total demand exceeds its own pixel capacity, no amount of
            # within-district rearranging can fit it. Cap it: irrigated is never touched
            # ("irrigation is always prioritised"), so every rainfed crop column is scaled by
            # the SAME single factor, preserving each crop's share of the rainfed total
            # exactly as reported, only bringing the overall level down to fit.
            if admin_level > 0 and values_R_local.size:
                capacity_for_rainfed = idx.size - float(sum_I_local.sum())
                rainfed_demand = float(values_R_local.sum())
                if rainfed_demand > capacity_for_rainfed + tolerance and rainfed_demand > 0:
                    cap_scale = max(capacity_for_rainfed, 0.0) / rainfed_demand
                    for col_idx, col in enumerate(rainfed_cols):
                        crop = col.rsplit("_", 1)[0]
                        original_ha = float(values_R_local[:, col_idx].sum()) * point_area_ha
                        if original_ha <= tolerance:
                            continue
                        final_ha = original_ha * cap_scale
                        shortfall_records.append({
                            "adm": group_label,
                            "crop": crop,
                            "Original_Rainfed_HA": original_ha,
                            "Scale_Factor": cap_scale,
                            "Final_Rainfed_HA": final_ha,
                            "Unallocated_HA": original_ha - final_ha,
                        })
                    values_R_local *= cap_scale
                    total_local = sum_I_local + np.sum(values_R_local, axis=1)
                    capacity_local = np.maximum(1.0 - total_local, 0.0)
                    print(
                        f"Capped rainfed demand in {group_label} to its own pixel capacity "
                        f"(scale factor {cap_scale:.4f}); see redistribution_capacity_shortfall.csv"
                    )

            while True:
                overloaded = np.where(total_local > 1.0 + tolerance)[0]
                if overloaded.size == 0:
                    break

                excess = total_local[overloaded] - 1.0
                order = np.argsort(-excess)
                source_idx = overloaded[order[0]]
                source_excess = excess[order[0]]
                if source_excess <= tolerance:
                    break

                sum_R_source = float(np.sum(values_R_local[source_idx])) if values_R_local.size else 0.0
                if sum_R_source <= tolerance:
                    # cannot move rainfed; scale down proportionally to meet constraint
                    src_sum = np.sum(values_I_local[source_idx]) + np.sum(values_R_local[source_idx])
                    if src_sum > 1.0 + tolerance:
                        scale = 1.0 / src_sum
                        if values_R_local.size:
                            values_R_local[source_idx] *= scale
                        if values_I_local.size:
                            values_I_local[source_idx] *= scale
                    sum_I_local = np.sum(values_I_local, axis=1) if values_I_local.size else np.zeros(idx.size)
                    total_local = sum_I_local + (np.sum(values_R_local, axis=1) if values_R_local.size else 0.0)
                    capacity_local = np.maximum(1.0 - total_local, 0.0)
                    continue

                has_other_receiver = tree is not None and np.any(
                    np.delete(capacity_local, source_idx) > tolerance
                )
                if not has_other_receiver:
                    allowed_rainfed = max(1.0 - sum_I_local[source_idx], 0.0)
                    cur_rainfed = sum_R_source
                    if cur_rainfed > allowed_rainfed + tolerance:
                        scale = allowed_rainfed / cur_rainfed if cur_rainfed > 0 else 0.0
                        values_R_local[source_idx] *= scale
                    total_local = sum_I_local + np.sum(values_R_local, axis=1)
                    capacity_local = np.maximum(1.0 - total_local, 0.0)
                    continue

                proportions = values_R_local[source_idx] / sum_R_source
                remaining = total_local[source_idx] - 1.0

                for recv_idx in _expanding_knn_receivers(tree, coords_local, capacity_local, source_idx):
                    if remaining <= tolerance:
                        break
                    accept = min(remaining, capacity_local[recv_idx])
                    if accept <= tolerance:
                        continue
                    delta = accept * proportions
                    values_R_local[source_idx] = np.maximum(values_R_local[source_idx] - delta, 0.0)
                    values_R_local[recv_idx] += delta
                    total_local[source_idx] -= accept
                    total_local[recv_idx] += accept
                    capacity_local[recv_idx] = max(1.0 - total_local[recv_idx], 0.0)
                    remaining -= accept

                if remaining > tolerance:
                    allowed_rainfed = max(1.0 - sum_I_local[source_idx], 0.0)
                    cur_rainfed = float(np.sum(values_R_local[source_idx]))
                    if cur_rainfed > allowed_rainfed + tolerance:
                        global_row = idx[source_idx]
                        error_msg = (
                            f"\n{'='*80}\n"
                            f"REDISTRIBUTION ERROR: Maximum redistribution potential reached\n"
                            f"{'='*80}\n"
                            f"Cannot redistribute all crop density to points with density > 1.0\n"
                            f"within {group_label}.\n"
                            f"At row index {global_row}, total density = {total_local[source_idx]:.4f} (> 1.0)\n"
                            f"Remaining excess: {remaining:.6f}\n\n"
                            f"This indicates that the cropland pixels in {group_label}\n"
                            f"do not have enough capacity for all the harvested areas\n"
                            f"even after redistribution.\n\n"
                            f"Possible solutions:\n"
                            f"  1. Change 'cropland_value' to include/exclude different pixel values\n"
                            f"  2. Update the land cover raster (land_cover.tif) with more recent data\n"
                            f"  3. Check FAOSTAT statistics for accuracy and completeness\n"
                            f"  4. Verify national/sub-national statistics are appropriate for the study year\n"
                            f"{'='*80}\n"
                        )
                        raise ValueError(error_msg)

                sum_I_local = np.sum(values_I_local, axis=1) if values_I_local.size else np.zeros(idx.size)
                total_local = sum_I_local + (np.sum(values_R_local, axis=1) if values_R_local.size else 0.0)
                capacity_local = np.maximum(1.0 - total_local, 0.0)

            values_I[idx] = values_I_local
            values_R[idx] = values_R_local

        # write back
        for idx_col, col in enumerate(irrigated_cols):
            gdf[col] = values_I[:, idx_col] if values_I.size else 0.0
        for idx_col, col in enumerate(rainfed_cols):
            gdf[col] = values_R[:, idx_col] if values_R.size else 0.0

        if admin_level > 0:
            shortfall_dir = os.path.join(Data_dir, "03.Final", "02.Calibration")
            os.makedirs(shortfall_dir, exist_ok=True)
            shortfall_fp = os.path.join(shortfall_dir, "redistribution_capacity_shortfall.csv")
            shortfall_df = pd.DataFrame(
                shortfall_records,
                columns=["adm", "crop", "Original_Rainfed_HA", "Scale_Factor", "Final_Rainfed_HA", "Unallocated_HA"],
            )
            shortfall_df.to_csv(shortfall_fp, index=False)
            if len(shortfall_df):
                print(
                    f"{shortfall_df['adm'].nunique()} admin unit(s) had their rainfed demand "
                    f"capped to fit their own pixel capacity; details in {shortfall_fp}"
                )
            else:
                print(f"No admin unit needed capping; wrote empty report to {shortfall_fp}")

        if os.path.exists(output_fp):
            os.remove(output_fp)
        gdf.to_file(
            output_fp,
            driver="GPKG",
            layer=f"{Country_name}_crop_IR_calibrated2",
            layer_options={"SPATIAL_INDEX": "NO"},
            engine="fiona",
        )

        return output_fp

    def track_crop_movements(
        Country_name: str,
        original_gpkg: str = None,
        redistributed_gpkg: str = None,
        output_csv: str = None,
        tolerance: float = 1e-6,
    ):
        """
        Infer and record where crop density was moved between two GeoPackages.

        This function compares `original_gpkg` and `redistributed_gpkg`, computes
        per-crop differences (redistributed - original) and greedily matches
        negative deltas (sources) to positive deltas (receivers) by nearest
        coordinates, producing a transfer log CSV with one row per inferred
        transfer: crop, source_id, source_x, source_y, target_id, target_x,
        target_y, amount_ha (in density units of the input layer).

        Returns:
            (output_csv_path, DataFrame)
        """
        if original_gpkg is None:
            original_gpkg = os.path.join(
                Data_dir,
                "03.Final",
                f"01.{Country_name}_crop_IR",
                f"{Country_name}_crop_IR_calibrated1.gpkg",
            )
        if redistributed_gpkg is None:
            redistributed_gpkg = os.path.join(
                Data_dir,
                "03.Final",
                f"01.{Country_name}_crop_IR",
                f"{Country_name}_crop_IR_calibrated2.gpkg",
            )
        if output_csv is None:
            out_dir = os.path.join(Data_dir, "03.Final", f"01.{Country_name}_crop_IR")
            os.makedirs(out_dir, exist_ok=True)
            output_csv = os.path.join(out_dir, f"Calibrated_crops_{Country_name}.csv")

        if not os.path.exists(original_gpkg):
            raise FileNotFoundError(f"Original GeoPackage not found: {original_gpkg}")
        if not os.path.exists(redistributed_gpkg):
            raise FileNotFoundError(f"Redistributed GeoPackage not found: {redistributed_gpkg}")

        g_orig = gpd.read_file(original_gpkg)
        g_red = gpd.read_file(redistributed_gpkg)

        # ensure x/y columns
        for g in (g_orig, g_red):
            if 'x' not in g.columns or 'y' not in g.columns:
                if g.geometry.is_empty.any():
                    raise ValueError("Point geometries required to compute x/y coordinates.")
                g['x'] = g.geometry.x
                g['y'] = g.geometry.y

        # determine id column
        if 'id' in g_orig.columns:
            ids = g_orig['id'].astype(str)
        elif 'pixel_id' in g_orig.columns:
            ids = g_orig['pixel_id'].astype(str)
        else:
            ids = pd.Series(g_orig.index.astype(str), index=g_orig.index)

        # identify crop columns (same logic as redistribution)
        cols = list(g_orig.columns)
        if 'farms' not in cols:
            raise ValueError("Original GeoPackage must contain a 'farms' column.")
        farms_index = cols.index('farms')
        crop_cols = [
            c for c in cols[farms_index + 1 :] if isinstance(c, str) and re.match(r"^[A-Z]{4}_[IR]$", c)
        ]
        if not crop_cols:
            raise ValueError("No crop columns found after the 'farms' column in original GeoPackage.")

        # ensure redistributed has same crop columns (if missing, treat as zeros)
        for c in crop_cols:
            if c not in g_red.columns:
                g_red[c] = 0.0

        coords = np.vstack([g_orig['x'].to_numpy(), g_orig['y'].to_numpy()]).T

        records = []

        for crop in crop_cols:
            delta = g_red[crop].astype(float).fillna(0.0).to_numpy() - g_orig[crop].astype(float).fillna(0.0).to_numpy()
            sources_idx = np.where(delta < -tolerance)[0]
            sinks_idx = np.where(delta > tolerance)[0]
            if sources_idx.size == 0 or sinks_idx.size == 0:
                continue

            sources = {i: -float(delta[i]) for i in sources_idx}
            sinks = {j: float(delta[j]) for j in sinks_idx}

            # for each source, match to nearest sinks greedily
            for src in sorted(sources.keys(), key=lambda i: -sources[i]):
                remaining = sources[src]
                if remaining <= tolerance:
                    continue

                # compute distances to all sinks with remaining capacity
                sink_idxs = [j for j, amt in sinks.items() if amt > tolerance]
                if not sink_idxs:
                    break
                deltas = coords[sink_idxs] - coords[src]
                dists = np.hypot(deltas[:, 0], deltas[:, 1])
                order = np.argsort(dists)

                for idx in order:
                    j = sink_idxs[idx]
                    avail = sinks[j]
                    if avail <= tolerance:
                        continue
                    transfer = min(remaining, avail)
                    if transfer <= tolerance:
                        continue

                    records.append(
                        {
                            'crop': crop,
                            'source_id': str(ids.iloc[src]) if hasattr(ids, 'iloc') else str(ids[src]),
                            'source_x': float(coords[src, 0]),
                            'source_y': float(coords[src, 1]),
                            'target_id': str(ids.iloc[j]) if hasattr(ids, 'iloc') else str(ids[j]),
                            'target_x': float(coords[j, 0]),
                            'target_y': float(coords[j, 1]),
                            'amount': float(transfer),
                        }
                    )

                    # update remaining and sinks
                    remaining -= transfer
                    sinks[j] = max(sinks[j] - transfer, 0.0)
                    if remaining <= tolerance:
                        break

        df_rec = pd.DataFrame.from_records(records, columns=['crop','source_id','source_x','source_y','target_id','target_x','target_y','amount'])
        os.makedirs(os.path.dirname(output_csv), exist_ok=True)
        df_rec.to_csv(output_csv, index=False)

        return output_csv, df_rec



    def create_final_cropland_map(
        Country_name: str,
        input_gpkg: str = None,
        output_dir: str = None,
        output_name: str = None,
        landcover_tif: str = None,
    ):
        """
        Create the final cropland GeoPackage from the calibrated crop density layer.

        The function reads the calibrated GeoPackage, reclassifies each crop row into
        commercial (H) or traditional (L) systems based on the "farms" column, converts
        density values into harvested area in hectares using the original landcover
        raster pixel size, and writes a final GeoPackage named {Country_name}_cropland.gpkg
        in the 03.Final folder.
        """
        if input_gpkg is None:
            input_gpkg = os.path.join(
                Data_dir,
                "03.Final",
                f"01.{Country_name}_crop_IR",
                f"{Country_name}_crop_IR_calibrated2.gpkg",
            )
        if output_dir is None:
            output_dir = os.path.join(Data_dir, "03.Final")
        os.makedirs(output_dir, exist_ok=True)

        if output_name is None:
            output_name = f"{Country_name}_cropland.gpkg"

        if landcover_tif is None:
            landcover_tif = os.path.join(Input_data, f"{Country_name}_LandCover.tif")

        output_fp = os.path.join(output_dir, output_name)

        if not os.path.exists(input_gpkg):
            raise FileNotFoundError(f"Input calibrated GeoPackage not found: {input_gpkg}")
        if not os.path.exists(landcover_tif):
            raise FileNotFoundError(f"Landcover raster not found: {landcover_tif}")

        with rasterio.open(landcover_tif) as lc:
            transform = lc.transform
            pixel_area_m2 = abs(transform.a * transform.e)
            point_area_ha = pixel_area_m2 / 10000.0

        gdf = gpd.read_file(input_gpkg)
        if "farms" not in gdf.columns:
            raise ValueError("Input GeoPackage must contain a 'farms' column.")

        cols = list(gdf.columns)
        farms_index = cols.index("farms")
        crop_cols = [
            c
            for c in cols[farms_index + 1 :]
            if isinstance(c, str) and re.match(r"^[A-Z]{4}_[IR]$", c)
        ]
        if not crop_cols:
            raise ValueError("No crop columns found after the 'farms' column.")

        # Ensure x/y columns exist
        if "x" not in gdf.columns or "y" not in gdf.columns:
            if gdf.geometry.is_empty.any():
                raise ValueError("Point geometries are required to compute x/y coordinates.")
            gdf["x"] = gdf.geometry.x
            gdf["y"] = gdf.geometry.y

        # Normalize farms values for row-wise processing
        farms = gdf["farms"].astype(str).fillna("NoFarm").str.upper().str.strip()
        crop_values = gdf[crop_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)

        # Convert density values into harvested area (ha)
        crop_values = crop_values * float(point_area_ha)

        new_columns = {}
        for row_idx, farm_value in farms.items():
            row_values = crop_values.loc[row_idx]
            assigned_source = set()

            if farm_value != "NOFARM":
                farm_match = re.match(r"^([A-Z]{4})_H([IR])$", farm_value)
                other_match = re.match(r"^OTHER_H([IR])$", farm_value)

                if farm_match:
                    crop_code, system = farm_match.groups()
                    source_col = f"{crop_code}_{system}"
                    if source_col in crop_values.columns:
                        value = float(row_values[source_col])
                        if value > 0.0:
                            dest_col = f"{crop_code}_H{system}"
                            new_columns.setdefault(dest_col, np.zeros(len(gdf), dtype=float))[row_idx] = value
                            assigned_source.add(source_col)

                elif other_match:
                    system = other_match.group(1)
                    for source_col in crop_cols:
                        if source_col.endswith(f"_{system}"):
                            value = float(row_values[source_col])
                            if value > 0.0:
                                crop_code = source_col.split("_")[0]
                                dest_col = f"{crop_code}_H{system}"
                                new_columns.setdefault(dest_col, np.zeros(len(gdf), dtype=float))[row_idx] = value
                                assigned_source.add(source_col)

            # Assign any remaining crop values to the traditional L system
            for source_col in crop_cols:
                value = float(row_values[source_col])
                if value <= 0.0 or source_col in assigned_source:
                    continue
                crop_code, system = source_col.split("_")
                dest_col = f"{crop_code}_L{system}"
                new_columns.setdefault(dest_col, np.zeros(len(gdf), dtype=float))[row_idx] = value

        # Build final GeoDataFrame with id,x,y and the new crop columns
        final_cols = [c for c in ["id", "x", "y"] if c in gdf.columns]
        final_gdf = gdf.loc[:, [c for c in final_cols if c in gdf.columns] + ["geometry"]].copy()

        for col_name, values in new_columns.items():
            final_gdf[col_name] = values

        # Remove rows without any crop values after id,x,y
        if new_columns:
            crop_matrix = np.vstack(list(new_columns.values())).T
            non_empty = np.any(crop_matrix != 0.0, axis=1)
            final_gdf = final_gdf.loc[non_empty].copy()

        if os.path.exists(output_fp):
            os.remove(output_fp)

        final_gdf.to_file(
            output_fp,
            driver="GPKG",
            layer=f"{Country_name}_cropland",
            layer_options={"SPATIAL_INDEX": "NO"},
            engine="fiona",
        )

        return output_fp

    def create_irrigated_gpkg(
        Country_name: str,
        input_gpkg: str = None,
        output_dir: str = None,
        output_name: str = None,
    ):
        """
        Create a GeoPackage that contains only irrigated columns (columns ending with 'I').

        Reads the final cropland GeoPackage (default: Data/03.Final/{Country_name}_cropland.gpkg),
        keeps id, x, y, geometry and any columns that end with 'I', drops rows where
        all irrigated columns are zero, and writes {Country_name}_irrigated.gpkg in the same
        folder (or output_dir if provided).
        """
        if input_gpkg is None:
            input_gpkg = os.path.join(output_dir or os.path.join(Data_dir, "03.Final"), f"{Country_name}_cropland.gpkg")
        if output_dir is None:
            output_dir = os.path.join(Data_dir, "03.Final")
        os.makedirs(output_dir, exist_ok=True)

        if output_name is None:
            output_name = f"{Country_name}_irrigated.gpkg"

        output_fp = os.path.join(output_dir, output_name)

        if not os.path.exists(input_gpkg):
            raise FileNotFoundError(f"Final cropland GeoPackage not found: {input_gpkg}")

        gdf = gpd.read_file(input_gpkg)

        # Identify irrigated columns (names ending with 'I')
        irrigated_cols = [c for c in gdf.columns if isinstance(c, str) and c.endswith("I")]
        if not irrigated_cols:
            raise ValueError("No irrigated columns (ending with 'I') found in the input GeoPackage.")

        # Keep id,x,y if present, plus geometry and irrigated columns
        keep_base = [c for c in ["id", "x", "y"] if c in gdf.columns]
        cols_to_keep = keep_base + irrigated_cols + ["geometry"]
        cols_to_keep = [c for c in cols_to_keep if c in gdf.columns]

        out_gdf = gdf.loc[:, cols_to_keep].copy()

        # Drop rows where all irrigated crop columns are zero
        crop_cols = [c for c in out_gdf.columns if c not in keep_base and c != "geometry"]
        if crop_cols:
            vals = out_gdf[crop_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)
            mask_empty = (vals == 0).all(axis=1)
            out_gdf = out_gdf.loc[~mask_empty].copy()

        if os.path.exists(output_fp):
            os.remove(output_fp)

        out_gdf.to_file(
            output_fp,
            driver="GPKG",
            layer=f"{Country_name}_irrigated",
            layer_options={"SPATIAL_INDEX": "NO"},
            engine="fiona",
        )

        return output_fp



    def export_final_cropland_statistics(
        Country_name: str,
        input_gpkg: str = None,
        output_dir: str = None,
    ):
        """
        Export national statistics from the final cropland GeoPackage.

        Writes:
            - Final_stat.csv
            - Commercial_farming_share.csv
            - Irrigation_share.csv
        """
        if input_gpkg is None:
            input_gpkg = os.path.join(output_dir or os.path.join(Data_dir, "03.Final"), f"{Country_name}_cropland.gpkg")
        if output_dir is None:
            output_dir = os.path.join(Data_dir, "03.Final")
        os.makedirs(output_dir, exist_ok=True)

        if not os.path.exists(input_gpkg):
            raise FileNotFoundError(f"Final cropland GeoPackage not found: {input_gpkg}")

        gdf = gpd.read_file(input_gpkg)
        exclude = {"id", "x", "y", "farms", "geometry"}
        crop_cols = [c for c in gdf.columns if isinstance(c, str) and c not in exclude]
        if not crop_cols:
            raise ValueError("No crop columns found in final cropland GeoPackage.")

        values = gdf[crop_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)
        total_by_crop = values.sum(axis=0)
        total_area = float(total_by_crop.sum())
        commercial_area = float(total_by_crop[total_by_crop.index.str.contains(r"_H[IR]$")].sum())
        irrigation_area = float(total_by_crop[total_by_crop.index.str.contains(r"_[HL]I$")].sum())

        final_stat_fp = os.path.join(output_dir, "Final_stat.csv")
        final_stat_df = pd.DataFrame({"Crop": total_by_crop.index, "Harvested_area_ha": total_by_crop.values})
        final_stat_df.to_csv(final_stat_fp, index=False)

        commercial_share_fp = os.path.join(output_dir, "Commercial_farming_share.csv")
        commercial_share_df = pd.DataFrame(
            [
                {
                    "Commercial_harvested_area_ha": commercial_area,
                    "Total_harvested_area_ha": total_area,
                    "Commercial_share": commercial_area / total_area if total_area > 0 else 0.0,
                }
            ]
        )
        commercial_share_df.to_csv(commercial_share_fp, index=False)

        irrigation_share_fp = os.path.join(output_dir, "Irrigation_share.csv")
        irrigation_share_df = pd.DataFrame(
            [
                {
                    "Irrigated_harvested_area_ha": irrigation_area,
                    "Total_harvested_area_ha": total_area,
                    "Irrigation_share": irrigation_area / total_area if total_area > 0 else 0.0,
                }
            ]
        )
        irrigation_share_df.to_csv(irrigation_share_fp, index=False)

        return {
            "final_stat": final_stat_fp,
            "commercial_share": commercial_share_fp,
            "irrigation_share": irrigation_share_fp,
        }

class Water: 
    def __init__(self):
        pass

        """
        This class contains all the methods needed for estimating the crop water demand based on GAEZ_v5 data. 
    
        """

    @staticmethod
    def convert_crops_to_GAEZ(irrigated_crop_list, crop_code_csv=None):
        """Convert irrigated crop labels from MAPSPAM-style names to GAEZ crop codes.

        Parameters
        ----------
        irrigated_crop_list : list of str
            Crop names in the format 'XXXX_LI' or 'XXXX_HI'.
        crop_code_csv : str, optional
            Path to the crop code mapping CSV. If None, uses Input_data/Gaez_crop_code.csv.

        Returns
        -------
        list of str
            Converted crop names in the format 'CODE_LILM' or 'CODE_HILM'.
        """
        if crop_code_csv is None:
            crop_code_csv = os.path.join(Input_data, "Gaez_crop_code.csv")

        if not os.path.exists(crop_code_csv):
            raise FileNotFoundError(f"Crop code CSV not found: {crop_code_csv}")

        df = pd.read_csv(crop_code_csv, dtype=str).fillna("")
        if "MAPSPAM" not in df.columns or "Code" not in df.columns:
            raise ValueError(
                f"Crop code CSV must contain 'Code' and 'MAPSPAM' columns: {crop_code_csv}"
            )

        mapping = {
            row["MAPSPAM"].strip().upper(): row["Code"].strip().upper()
            for _, row in df.iterrows()
            if row["MAPSPAM"].strip() and row["Code"].strip()
        }

        result = []
        for name in irrigated_crop_list:
            if not isinstance(name, str) or "_" not in name:
                raise ValueError(
                    f"Invalid irrigated crop name format, expected 'XXXX_LI' or 'XXXX_HI': {name}"
                )

            crop, system = name.split("_", 1)
            crop = crop.strip().upper()
            system = system.strip().upper()

            if crop not in mapping:
                raise ValueError(
                    f"MAPSPAM crop code '{crop}' not found in {crop_code_csv}"
                )

            if system == "HI":
                system_code = "HILM"
            elif system == "LI":
                system_code = "LILM"
            else:
                raise ValueError(
                    f"Invalid irrigation system suffix, expected 'HI' or 'LI': {name}"
                )

            result.append(f"{mapping[crop]}_{system_code}")

        return result
    
    def build_gaez_cwd_urls(
        SSP,
        Climate_model,
        irrigated_crops_GAEZ,
        base_url=None,
    ):
        
        """
         This function builds urls for downloading GAEZ v5 data. More info are available here: https://github.com/un-fao/gaezv5/wiki/21.-GAEZ-v5-Platform-User-Guide 

        """
        if base_url is None:
            base_url = (
                "https://storage.googleapis.com/fao-gismgr-gaez-v5-data/DATA/GAEZ-V5/MAPSET/RES02-WDE/GAEZ-V5.RES02-WDE."
            )

        if str(SSP).lower().startswith("hist") or str(SSP).lower() == "historical":
            period_code = "HP0120"
            ssp_code = "HIST"
            climate_models = [Climate_model]
        else:
            period_code = "FP2140"
            ssp_map = {"ssp126": "SSP126", "ssp370": "SSP370", "ssp585": "SSP585"}
            ssp_code = ssp_map.get(str(SSP).lower(), str(SSP).upper())
            climate_models = [Climate_model]

        urls = []
        for climate in climate_models:
            for crop_entry in irrigated_crops_GAEZ:
                if not isinstance(crop_entry, str) or "_" not in crop_entry:
                    raise ValueError(f"Invalid irrigated crop name: {crop_entry}")

                crop_code, input_code = crop_entry.strip().upper().split("_", 1)
                if input_code not in ("LILM", "HILM"):
                    raise ValueError(
                        f"Invalid irrigation input suffix, expected LILM or HILM: {input_code}"
                    )

                filename = f"{period_code}.{climate}.{ssp_code}.{crop_code}.{input_code}.tif"
                url = f"{base_url}{filename}"
                urls.append({"crop_entry": crop_entry, "url": url, "filename": filename})

        return urls


    def download_gaez_rasters(url_records, output_folder, overwrite=False):
        os.makedirs(output_folder, exist_ok=True)

        downloaded = []
        skipped = []
        errors = []

        for rec in url_records:
            url = rec["url"]
            filename = rec["filename"]
            output_path = os.path.join(output_folder, filename)

            if os.path.exists(output_path) and not overwrite:
                skipped.append(output_path)
                print(f"Skipped (exists): {filename}")
                continue

            try:
                head = requests.head(url, allow_redirects=True, timeout=15)
                if head.status_code != 200:
                    errors.append((url, f"HEAD status {head.status_code}"))
                    print(f"URL unavailable: {url} (status {head.status_code})")
                    continue

                with requests.get(url, stream=True, timeout=60) as r:
                    r.raise_for_status()
                    with open(output_path, "wb") as dst:
                        for chunk in r.iter_content(chunk_size=8192):
                            if chunk:
                                dst.write(chunk)

                downloaded.append(output_path)
                print(f"Downloaded: {filename}")

            except requests.RequestException as exc:
                errors.append((url, str(exc)))
                print(f"Failed to download {url}: {exc}")
            except Exception as exc:
                errors.append((url, str(exc)))
                print(f"Failed to download {url}: {exc}")

        return {"downloaded": downloaded, "skipped": skipped, "errors": errors}

    def resample_cwd_to_points(
        Country_name=str,
        input_dir=None,
        cropland_gpkg=None,
        output_dir=None,
        out_nodata=None,
    ):
        """Sample reprojected crop water demand rasters onto the cropland point layer and save `cwd.gpkg`.
           The value of cwd is expressed in BCM/ha.

        Parameters
        ----------
        Country_name : str
            Country code for naming outputs.
        input_dir : str, optional
            Folder containing the reprojected crop water demand rasters. Defaults to
            Data/04.CropWaterDemand/02.Reprojected_Rasters.
        cropland_gpkg : str, optional
            Path to cropland point GeoPackage. Defaults to
            Data/03.Final/{Country_name}_irrigated.gpkg.
        output_dir : str, optional
            Folder where `cwd.gpkg` will be written. Defaults to
            Data/04.CropWaterDemand.
        out_nodata : numeric, optional
            Value to treat as nodata when sampling. Defaults to 0.

        Returns
        -------
        tuple
            (out_gpkg_path, GeoDataFrame)
        """
        if input_dir is None:
            input_dir = os.path.join(Data_dir, '04.CropWaterDemand', '02.Reprojected_Rasters')
        if cropland_gpkg is None:
            cropland_gpkg = os.path.join(Data_dir, '03.Final', f"{Country_name}_irrigated.gpkg")
        if output_dir is None:
            output_dir = os.path.join(Data_dir, '04.CropWaterDemand')
        os.makedirs(output_dir, exist_ok=True)

        if out_nodata is None:
            out_nodata = 0

        if not os.path.isdir(input_dir):
            raise FileNotFoundError(f"Input raster folder not found: {input_dir}")
        if not os.path.exists(cropland_gpkg):
            raise FileNotFoundError(f"Cropland point layer not found: {cropland_gpkg}")

        gdf = gpd.read_file(cropland_gpkg)
        if gdf.crs is None:
            raise ValueError("Cropland layer has no CRS")
        original_crs = gdf.crs

        raster_files = sorted(glob.glob(os.path.join(input_dir, "*.tif")))
        if not raster_files:
            raise FileNotFoundError(f"No reprojected rasters found in: {input_dir}")

        # Use pattern matching on GAEZ filenames like: HP0120.AGERA5.HIST.BANA.LILM.tif
        pattern = re.compile(r"^(?:[^.]+)\.(?:[^.]+)\.(?:[^.]+)\.(?P<crop>[A-Z]{4})\.(?P<input>LILM|HILM)\.tif$", re.IGNORECASE)

        # Ensure points are in raster CRS for sampling using the first raster
        with rasterio.open(raster_files[0]) as src0:
            raster_crs = src0.crs
            if raster_crs is None:
                raise ValueError(f"First raster has no CRS: {raster_files[0]}")
            if not gdf.crs.is_exact_same(raster_crs):
                gdf = gdf.to_crs(raster_crs)

        # Sample each raster and add a column to gdf
        for raster_path in raster_files:
            name = os.path.basename(raster_path)
            m = pattern.match(name)
            if not m:
                # skip files that don't match expected GAEZ pattern
                continue

            crop_code = m.group('crop').upper()
            input_code = m.group('input').upper()
            system = 'LI' if input_code == 'LILM' else 'HI'
            field_name = f"cwd_{crop_code}_{system}"

            with rasterio.open(raster_path) as src:
                nod = src.nodata if src.nodata is not None else out_nodata
                arr = src.read(1)
                transform = src.transform

                vals = []
                for pt in gdf.geometry:
                    # use point coords in raster CRS
                    try:
                        row, col = rasterio.transform.rowcol(transform, pt.x, pt.y)
                    except Exception:
                        vals.append(float(0.0))
                        continue

                    if 0 <= row < src.height and 0 <= col < src.width:
                        v = arr[row, col]
                    else:
                        v = nod

                    if v is None or (isinstance(v, float) and np.isnan(v)) or (nod is not None and v == nod):
                        vals.append(float(0.0))
                    else:
                        vals.append(float(v) * 0.00000001) # convert from mm to BCM/ha assuming 1 millimeter  = 1 liter per 1 square meter

            gdf[field_name] = np.array(vals, dtype=np.float32)

        # ensure x/y columns
        if 'x' not in gdf.columns or 'y' not in gdf.columns:
            gdf['x'] = gdf.geometry.x
            gdf['y'] = gdf.geometry.y

        # Convert back to original CRS for saving
        try:
            if original_crs is not None and not gdf.crs.is_exact_same(original_crs):
                gdf = gdf.to_crs(original_crs)
        except Exception:
            pass

        out_gpkg = os.path.join(output_dir, 'cwd.gpkg')
        if os.path.exists(out_gpkg):
            os.remove(out_gpkg)

        gdf.to_file(out_gpkg, driver='GPKG', layer='cwd', layer_options={'SPATIAL_INDEX': 'NO'}, engine='fiona')

        return out_gpkg, gdf
    
