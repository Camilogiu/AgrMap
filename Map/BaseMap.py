import os
import glob
import re
import shutil
import requests
import pandas as pd
import numpy as np
import rasterio
import geopandas as gpd
from shapely.geometry import Point
from rasterio.features import rasterize
from rasterio.warp import calculate_default_transform
import rasterio.transform
from rasterio.mask import mask
from rasterio.warp import reproject, Resampling
from pyproj import CRS

ROOT_DIR = os.path.abspath(os.curdir)
Data_dir = os.path.join(ROOT_DIR, "Data")
Input_data = os.path.join(Data_dir, "00.InputData")

class Raster:
    def __init__(self):
        pass

        """
        This class contains all the methods needed for reclassifying,
        clipping, reprojecting, converting and filtering all the rasters
        used for the analysis. This class is used to run the steps 1-5 of the notebook. 

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
        cropland_value : int
            Pixel value representing cropland in the input raster.
        out_nodata : int
            Nodata value for the output raster.

        Returns
        -------
        str
            Path to the output raster.
        """
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
            out[data == cropland_value] = 1

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
        
        gdf.to_file(out_fp, driver="GPKG")
        
        print(f"Saved point vector: {os.path.basename(out_fp)} with {len(gdf)} points")
        
        return [out_fp]
    
    def rasterize_farmlands(
        Country_name=str,
        reference_raster=None,
        farmland_shapefile=None,
        class_lookup_csv=None,
        target_vector=None,
        output_dir=None,
        out_nodata=255,
        target_layer=None,
    ):
        """
        Rasterize farmland polygons to the same grid as the reference land cover raster,
        then sample those values on an existing GeoPackage and add a "farms" column.

        Missing/no-farm pixels will have "NoFarm" in the 'farms' column.
        """
        output_dir = os.path.join(Data_dir, "01.BaseCroplandLayer") if output_dir is None else output_dir
        os.makedirs(output_dir, exist_ok=True)

        if reference_raster is None:
            reference_raster = os.path.join(Input_data, f"{Country_name}_LandCover.tif")
        if farmland_shapefile is None:
            farmland_shapefile = os.path.join(Input_data, f"{Country_name}_farmlands.shp")
        if class_lookup_csv is None:
            class_lookup_csv = os.path.join(Input_data, f"{Country_name}Farmlands.csv")
        if target_vector is None:
            target_vector = os.path.join(output_dir, f"{Country_name}.gpkg")

        if not os.path.exists(reference_raster):
            raise FileNotFoundError(f"Reference raster not found: {reference_raster}")
        if not os.path.exists(farmland_shapefile):
            raise FileNotFoundError(f"Farmland shapefile not found: {farmland_shapefile}")
        if not os.path.exists(class_lookup_csv):
            raise FileNotFoundError(f"Lookup CSV not found: {class_lookup_csv}")
        if not os.path.exists(target_vector):
            raise FileNotFoundError(f"Target vector not found: {target_vector}")

        # read lookup (expects columns "Farmland" and "Class")
        lookup = pd.read_csv(class_lookup_csv, dtype=str)
        try:
            class_to_name = {int(row["Class"]): row["Farmland"] for _, row in lookup.iterrows()}
        except Exception:
            # tolerant fallback: try swapped column names
            class_to_name = {int(row["Class"]): row.get("Farmland", "") for _, row in lookup.iterrows()}

        farmlands = gpd.read_file(farmland_shapefile)
        with rasterio.open(reference_raster) as src:
            if farmlands.crs != src.crs:
                farmlands = farmlands.to_crs(src.crs)

            shapes = []
            for _, row in farmlands.iterrows():
                crop_class = row.get("crop_class", None)
                if crop_class is None or pd.isna(crop_class):
                    continue
                try:
                    crop_class_value = int(crop_class)
                except Exception:
                    # skip invalid values
                    continue
                shapes.append((row.geometry, crop_class_value))

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
                layer_creation_options=["SPATIAL_INDEX=NO"],
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
                layer_creation_options=["SPATIAL_INDEX=NO"],
            )
            os.replace(tmp_fp, output_gpkg)

        return farmland_raster_path, output_gpkg
    
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
        clipped_dir=None,
        output_dir=None,
        out_nodata=255,
        target_crs=None,
    ):
        """
        Reproject clipped rasters to a target CRS without matching any reference grid.
        
        Args:
            Country_name: Country identifier
            clipped_dir: Path to clipped rasters directory
            output_dir: Output root directory
            out_nodata: NoData value for output rasters
            target_crs: Target CRS (pyproj.CRS, EPSG string, or int). If None, uses source CRS.
        
        Returns:
            reprojected_dir: Path to output reprojected rasters directory
            reprojected_files: List of created reprojected raster paths
        """
        
        
        output_root = os.path.join(Data_dir, f"02.{Country_name}_MAPSPAM_Crops") if output_dir is None else output_dir
        reprojected_dir = os.path.join(output_root, "02.Reprojected_Rasters")
        os.makedirs(reprojected_dir, exist_ok=True)

        if clipped_dir is None:
            clipped_dir = os.path.join(output_root, f"01.{Country_name}_Clipped_Rasters")

        if not os.path.isdir(clipped_dir):
            raise FileNotFoundError(f"Clipped MAPSPAM folder not found: {clipped_dir}")

        tif_files = sorted(glob.glob(os.path.join(clipped_dir, "*.tif")))
        if not tif_files:
            raise FileNotFoundError(f"No .tif files found in: {clipped_dir}")
        
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

            #share_r = float(total_R / total_A) if total_A > 0 else 0.0
            #if share_r >= 0.95:
                #share_r = 1.0
            #elif share_r < 0.05:
                #share_r = 0.0
            #else:
                #share_r = round(share_r, 2)

            rows.append({
                "crop": crop_code,
                "I": total_I,
                "R": total_R,
                "A": total_A,
                "Share_I": share_i,
                #"Share_R": share_r,
            })

            # Only copy I raster if total_I > 0
            if total_I > 0 and i_fp:
                shutil.copy2(i_fp, os.path.join(filtered_dir, os.path.basename(i_fp)))
            
            # Only copy R raster if total_R > 0
            if total_R > 0 and r_fp:
                shutil.copy2(r_fp, os.path.join(filtered_dir, os.path.basename(r_fp)))

        df = pd.DataFrame(rows, columns=["crop", "I", "R", "A", "Share_I"])
        df.to_csv(summary_csv, index=False)

        return filtered_dir, summary_csv, df

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
            layer_creation_options=["SPATIAL_INDEX=NO"],
        )

        print(f"Saved resampled point vector: {os.path.basename(out_gpkg)} with {len(gdf)} points")
        return output_dir, out_gpkg, gdf


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
            layer_creation_options=["SPATIAL_INDEX=NO"],
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
    ):
        """
        This function calculates the total harvested area per crop from the created crop_IT_filtered.gpkg. 
        The function multiplies crop's density by the point area (corresponding to the landcover pixel area) to get the harvested are (in hectares) per point. 

        Saves in out_dir (default Data/03.Final/02.Calibration):
         - Sum_MAPSPAM_I.csv
         - Sum_MAPSPAM_R.csv

        Returns dict with output paths and dataframes.
        """
    
        if landcover_tif is None:
            landcover_tif = os.path.join(Input_data, f"{Country_name}_LandCover.tif")
        if out_dir is None:
            out_dir = os.path.join(Data_dir, "03.Final", "02.Calibration")
        os.makedirs(out_dir, exist_ok=True)

        # read filtered points gpkg
        if not os.path.exists(input_gpkg):
            raise FileNotFoundError(f"Input gpkg not found: {input_gpkg}")
        gdf = gpd.read_file(input_gpkg)

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

        # Sum MAPSPAM area per crop/system: Area = density * point_area_ha
        sums_I = {}
        sums_R = {}
        for col in crop_cols:
            crop = col.split("_")[0].upper()
            system = col.split("_")[1].upper()
            vals = pd.to_numeric(gdf[col].fillna(0), errors="coerce").fillna(0.0).astype(float)
            total_ha = float(vals.sum() * point_area_ha)
            if system == "I":
                sums_I[crop] = sums_I.get(crop, 0.0) + total_ha
            else:
                sums_R[crop] = sums_R.get(crop, 0.0) + total_ha

        sumI_df = pd.DataFrame(sorted(sums_I.items()), columns=["crop", "MAPSPAM_HA"])
        sumR_df = pd.DataFrame(sorted(sums_R.items()), columns=["crop", "MAPSPAM_HA"])
        #sumT_df = (
            #pd.concat([sumI_df.assign(type="I"), sumR_df.assign(type="R")], ignore_index=True)
            #.groupby("crop", as_index=False)["MAPSPAM_HA"]
            #.sum()
        #)

        sumI_fp = os.path.join(out_dir, "Sum_MAPSPAM_Irrigated.csv")
        sumR_fp = os.path.join(out_dir, "Sum_MAPSPAM_Rainfed.csv")
        #sumT_fp = os.path.join(out_dir, "Sum_MAPSPAM_total.csv")
        
        sumI_df.to_csv(sumI_fp, index=False)
        sumR_df.to_csv(sumR_fp, index=False)
        #sumT_df.to_csv(sumT_fp, index=False)

        return (f"Total MAPSPAM harvested area per irrigated and rainfed crop saved to:\n  {sumI_fp}\n  {sumR_fp}\n")
    
    def calculate_national_statistics(
            Country_name: str,
            cropnames_csv: str = None,
            harvested_csv: str = None,
            out_dir: str = None,
    ):

        """
        This function calculates the total harvested area per crop estimated from national statistics. 
        The function returns a .csv file with a consistent naming convention to MAPSPAM. These data are needed 
        for the final calibration. 

        Saves in out_dir (default Data/03.Final/02.Calibration):
         - National_Harvested_{Country_name}.csv
         - National_irrigated_crops_{Country_name}.csv
         - National_rainfed_crops_{Country_name}.csv
        """
        if cropnames_csv is None:
            cropnames_csv = os.path.join(Input_data, "CropNames.csv")
        if harvested_csv is None:
            harvested_csv = os.path.join(Input_data, f"HarvestedArea{Country_name}.csv")
        if out_dir is None:
            out_dir = os.path.join(Data_dir, "03.Final", "02.Calibration")
        os.makedirs(out_dir, exist_ok=True)

        if not os.path.exists(cropnames_csv):
            raise FileNotFoundError(f"Crop names CSV not found: {cropnames_csv}")
        if not os.path.exists(harvested_csv):
            raise FileNotFoundError(f"Harvested area CSV not found: {harvested_csv}")

        crop_map = pd.read_csv(cropnames_csv, dtype=str).fillna("")
        if {"Crop", "MAPSPAM"}.issubset(set(crop_map.columns)):
            mapping = crop_map[["Crop", "MAPSPAM"]].copy()
        else:
            mapping = crop_map.iloc[:, :2].copy()
            mapping.columns = ["Crop", "MAPSPAM"]

        mapping["Crop"] = mapping["Crop"].astype(str).str.strip()
        mapping["MAPSPAM"] = mapping["MAPSPAM"].astype(str).str.upper().str.strip()

        harvested = pd.read_csv(harvested_csv, dtype=str).fillna("")
        if "Crop" in harvested.columns:
            crop_col = "Crop"
            value_col = next((c for c in harvested.columns if c != "Crop"), None)
        else:
            crop_col = harvested.columns[0]
            value_col = harvested.columns[1] if len(harvested.columns) > 1 else None

        if value_col is None:
            raise ValueError(f"Harvested CSV must contain crop name and area columns: {harvested_csv}")

        harvested = harvested[[crop_col, value_col]].copy()
        harvested.columns = ["Crop", "Area"]
        harvested["Crop"] = harvested["Crop"].astype(str).str.strip()
        harvested["Area"] = pd.to_numeric(harvested["Area"].astype(str).str.replace(",", ""), errors="coerce").fillna(0.0)

        merged = harvested.merge(mapping, on="Crop", how="left")
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

    def calculate_scaling_factor(Country_name: str):

        """
        This function calculates the scaling factor for each crop and system (irrigated/rainfed) 
        by dividing the national harvested area by the MAPSPAM harvested area.

        Reads:
         - National_irrigated_crops_{Country_name}.csv
         - National_rainfed_crops_{Country_name}.csv
         - Sum_MAPSPAM_Irrigated.csv
         - Sum_MAPSPAM_Rainfed.csv

        Writes:
         - scaling_factor_irrigated.csv
         - scaling_factor_rainfed.csv
        """
        out_dir = os.path.join(Data_dir, "03.Final", "02.Calibration")
        os.makedirs(out_dir, exist_ok=True)

        national_irr_fp = os.path.join(out_dir, f"National_irrigated_crops_{Country_name}.csv")
        national_rain_fp = os.path.join(out_dir, f"National_rainfed_crops_{Country_name}.csv")
        mapspam_i_fp = os.path.join(out_dir, "Sum_MAPSPAM_Irrigated.csv")
        mapspam_r_fp = os.path.join(out_dir, "Sum_MAPSPAM_Rainfed.csv")

        if not os.path.exists(national_irr_fp):
            raise FileNotFoundError(f"National irrigated CSV not found: {national_irr_fp}")
        if not os.path.exists(national_rain_fp):
            raise FileNotFoundError(f"National rainfed CSV not found: {national_rain_fp}")
        if not os.path.exists(mapspam_i_fp):
            raise FileNotFoundError(f"MAPSPAM irrigated summary CSV not found: {mapspam_i_fp}")
        if not os.path.exists(mapspam_r_fp):
            raise FileNotFoundError(f"MAPSPAM rainfed summary CSV not found: {mapspam_r_fp}")

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

        def safe_scale(national_area, mapspam_area, crop, system):
            national_area = float(national_area)
            mapspam_area = float(mapspam_area)
            if national_area <= 0.0:
                return 0.0
            if mapspam_area <= 0.0:
                print(f"Warning: MAPSPAM {system} area is zero or missing for crop '{crop}'; setting scaling factor to 1.0")
                return 1.0
            return national_area / mapspam_area

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

        scaling_i_df = _build_scaling_df(national_irr, mapspam_i, "irrigated")
        scaling_r_df = _build_scaling_df(national_rain, mapspam_r, "rainfed")

        scaling_i_fp = os.path.join(out_dir, "scaling_factor_irrigated.csv")
        scaling_r_fp = os.path.join(out_dir, "scaling_factor_rainfed.csv")
        scaling_i_df.to_csv(scaling_i_fp, index=False)
        scaling_r_df.to_csv(scaling_r_fp, index=False)

        return scaling_i_fp, scaling_r_fp

    def calibrate_crops(
        Country_name: str,
        input_gpkg: str = None,
        scaling_i_fp: str = None,
        scaling_r_fp: str = None,
        output_dir: str = None,
    ):
        """
        Calibrate crop density points using irrigated and rainfed scaling factors.

        Reads the input crop GeoPackage and the two scaling factor CSVs.
        Drops any crop column whose scaling factor is missing or zero.
        Multiplies remaining crop values by the corresponding scaling factor.
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

        def _read_scaling_csv(path):
            df = pd.read_csv(path, dtype=str).fillna("")
            cols = {c.lower(): c for c in df.columns}
            crop_col = next((cols[k] for k in ["crop"] if k in cols), None)
            factor_col = next(
                (cols[k] for k in ["scaling_factor", "scalingfactor", "factor", "value"] if k in cols),
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

        output_fp = os.path.join(output_dir, f"{Country_name}_crop_IR_calibrated1.gpkg")
        if os.path.exists(output_fp):
            os.remove(output_fp)

        gdf.to_file(
            output_fp,
            driver="GPKG",
            layer=f"{Country_name}_crop_IR_calibrated1",
            layer_creation_options=["SPATIAL_INDEX=NO"],
        )

        return output_fp

    def redistribute_crop_density(
        Country_name: str,
        input_gpkg: str = None,
        output_dir: str = None,
        out_gpkg_name: str = None,
        target_crs=None,
        tolerance: float = 1e-6,
    ):
        """
        Redistribute crop density so every point has total density <= 1.0.

        The algorithm preserves the original crop composition of excess density
        from overloaded points and transfers it to the nearest available points
        with remaining capacity.

        Parameters
        ----------
        target_crs : pyproj.CRS or str or int, optional
            If provided, reprojects the output GeoDataFrame to this CRS before
            saving the calibrated GeoPackage.
        """
        if input_gpkg is None:
            input_gpkg = os.path.join(
                Data_dir,
                "03.Final",
                f"01.{Country_name}_crop_IR",
                f"{Country_name}_crop_IR_calibrated1.gpkg",
            )
        if output_dir is None:
            output_dir = os.path.join(Data_dir, "03.Final", f"01.{Country_name}_crop_IR")
        os.makedirs(output_dir, exist_ok=True)

        if out_gpkg_name is None:
            out_gpkg_name = f"{Country_name}_crop_IR_calibrated2.gpkg"

        output_fp = os.path.join(output_dir, out_gpkg_name)
        if not os.path.exists(input_gpkg):
            raise FileNotFoundError(f"Input GeoPackage not found: {input_gpkg}")

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
            c
            for c in cols[farms_index + 1 :]
            if isinstance(c, str) and re.match(r"^[A-Z]{4}_[IR]$", c)
        ]
        if not crop_cols:
            raise ValueError("No crop columns found after the 'farms' column.")

        numeric = gdf[crop_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0).astype(float)
        values = numeric.to_numpy(dtype=float)
        row_sum = np.sum(values, axis=1)
        capacity = np.maximum(1.0 - row_sum, 0.0)

        coords = np.array([[geom.x, geom.y] for geom in gdf.geometry])
        if coords.size == 0:
            raise ValueError("Input GeoPackage contains no geometries.")

        def get_receivers(exclude_idx=None):
            receiver_mask = capacity > tolerance
            if exclude_idx is not None:
                receiver_mask[exclude_idx] = False
            return np.where(receiver_mask)[0]

        def distance_order(source_idx, receiver_idxs):
            if receiver_idxs.size == 0:
                return receiver_idxs
            delta = coords[receiver_idxs] - coords[source_idx]
            dist = np.hypot(delta[:, 0], delta[:, 1])
            return receiver_idxs[np.argsort(dist)]

        overloaded = np.where(row_sum > 1.0 + tolerance)[0]
        if overloaded.size == 0:
            if os.path.exists(output_fp):
                os.remove(output_fp)
            gdf.to_file(
                output_fp,
                driver="GPKG",
                layer=f"{Country_name}_crop_IR_calibrated2",
                layer_creation_options=["SPATIAL_INDEX=NO"],
            )
            return output_fp

        # Process overloaded points in descending order of excess density
        while True:
            row_sum = np.sum(values, axis=1)
            overloaded = np.where(row_sum > 1.0 + tolerance)[0]
            if overloaded.size == 0:
                break

            excess = row_sum[overloaded] - 1.0
            order = np.argsort(-excess)
            source_idx = overloaded[order[0]]
            source_excess = excess[order[0]]
            source_sum = row_sum[source_idx]
            if source_sum <= 1.0 + tolerance:
                break

            receivers = get_receivers(exclude_idx=source_idx)
            if receivers.size == 0:
                # No available receivers; scale down the source point to 1.0
                scale = 1.0 / source_sum
                values[source_idx] *= scale
                continue

            receiver_order = distance_order(source_idx, receivers)
            proportions = values[source_idx] / source_sum
            remaining = source_sum - 1.0

            for recv_idx in receiver_order:
                if remaining <= tolerance:
                    break

                transfer = min(remaining, capacity[recv_idx])
                if transfer <= tolerance:
                    continue

                delta = transfer * proportions
                values[source_idx] = np.maximum(values[source_idx] - delta, 0.0)
                values[recv_idx] += delta

                row_sum[source_idx] -= transfer
                row_sum[recv_idx] += transfer
                capacity[recv_idx] = max(1.0 - row_sum[recv_idx], 0.0)
                remaining -= transfer

            if remaining > tolerance:
                # If all receivers are exhausted, reduce the source proportionally to meet the constraint
                source_sum = np.sum(values[source_idx])
                if source_sum > 1.0 + tolerance:
                    scale = 1.0 / source_sum
                    values[source_idx] *= scale

            capacity = np.maximum(1.0 - np.sum(values, axis=1), 0.0)

        # Write redistributed values back into GeoDataFrame
        for col_idx, col_name in enumerate(crop_cols):
            gdf[col_name] = values[:, col_idx]

        if os.path.exists(output_fp):
            os.remove(output_fp)

        gdf.to_file(
            output_fp,
            driver="GPKG",
            layer=f"{Country_name}_crop_IR_calibrated2",
            layer_creation_options=["SPATIAL_INDEX=NO"],
        )

        return output_fp

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
            layer_creation_options=["SPATIAL_INDEX=NO"],
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
            layer_creation_options=["SPATIAL_INDEX=NO"],
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

