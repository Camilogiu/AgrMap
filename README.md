# AgrMap

A geospatial workflow for generating a **base-year cropland map** by combining land cover data, MAPSPAM crop distribution datasets developed by the IFPRI research institute, Open Street Map (OSM) farmland shapefiles, and national agricultural statistics .

The workflow produces spatially explicit estimates of:

* High-input irrigated area (HI)
* High-input rainfed area (HR)
* Low-input irrigated area (LI)
* Low-input rainfed area (LR)

The farming types are consistent with the FAO-IIASA Global Agro-Ecological Zones (GAEZ) database. Check the definition in "Input Levels" in https://s3.eu-west-1.amazonaws.com/data.gaezdev.aws.fao.org/documentation/GAEZ4_Glossary.pdf 

The final output is a point-layer base-year agricultural map that can be used as input for agricultural, energy, land-use, or food-system modelling.

---
---

## Prerequisites

1. Install Anaconda:  
   https://www.anaconda.com/products/distribution

2. Clone or download this repository

3. Create and activate the environment:
```bash
conda env create -f environment.yml
conda activate BaseMap
```
---

# Workflow Overview

The workflow consists of nine main steps:

1. Creating the base cropland layer. 
2. Adding farmland information. 
3. Processing MAPSPAM's crop rasters. 
4. Calculating the share of irrigated area. 
5. Generate Density rasters.
6. Resample the rasters to the cropland point layer (created in step 1).
7. Cropland calibration. 
8. Assigning farming systems. 
9. Adding crop water demand.
For further information about each step, see the Wokflow section below.

All the input and output data are stored into the folder

```text
Data/
```

---
# The notebooks

In this repository there are two notebooks: 
* BaseMap.ipynb
* FarmlandClassification.ipynb

The main notebook is BaseMap.ipynb. FarmlandClassification.ipynb is used to classify Open Street Map's data on farmlands. OSM provides vector polygons with farmlands, specifying the crop-type and if it is irrigated or not. The polygon has to be reclassified by the user in order to follow **the same naming convention of MAPSPAM**.

Follow the guidelined on FarmlandClassification.ipynb to download OSM data and run the notebook to  re-classify OSM farmland polygons. 

---
# Input Data

In the Data directory there is a folder named:

```text
00.InputData/
```
In this folder you will find: 
* MAPSPAM crop rasters developed by the International Food Policy Research Institute (IFPRI) in a folder name "spam2020V2r2_global_harvested_area" sourced from https://www.mapspam.info/data/ 
* National agricultural statistics from FAOSTAT, sourced from https://www.fao.org/faostat/en/#data/QCL . FAOSTAT csv files quantify global harvested aread divided by country and crop for the year 2023. 
* Two look-up tables called CropNames.csv and GAEZ_crop_code to ensure consistency across different naming conventions used for crops.

Additionally, add the following country-specific data: 


## Spatial Data

* Country boundary shapefile named as *gadm41_{Country_name}*: https://gadm.org/download_country.html

For instance for Zambia it is gadm41_ZMB.shp
Note: make sure the country boundary shapefile has the same reference system as MAPSPAM (EPSG:4326 (WGS 84))

* Land cover raster
This raster should be put inside a folder named *Land Cover* and named as *{Country_name}_LandCover*. For instance for Zambia we used *ZMB_LandCover*.  In the test folder we used Copernicus Global Land Cover dataset (2023), accessed through the QGIS Copernicus plugin. The same dataset can be downloaded from https://cds.climate.copernicus.eu/datasets/satellite-land-cover?tab=overview 

The land cover classification is based on the UN FAO Land Cover Classification System (LCCS).

* Sub-national administrative boundaries (optional)
This is needed for the calibration (step 7) if data at adm1 or admn2 level are available. You can ignore if these data are not availble and select Calibration_method = 0 in the User Settings cell. 
You can download sub-national administrative shapefiles from https://data.humdata.org/dataset/cod-ab-global
Once you have downloaded the folder for your country, add the {Country_name}_admin1.shp if you can calibrate at adm 1 and Calibration_method = 1 or {Country_name}_admin2.shp file if you can calibrate at admn 2 and Calibration_method = 2.

* OSM farmland layer, accessed through the QGIS OSM plugin and renamed as *OSM_farmland*. See more information on how to download OSM data on the FarmalandClassification Jupyter Notebook.

## Statistics (optional)
If statistics at amd1 or amd2 level are available, and you have added Sub-national administrative boundaries in your input data, then you need to create a .csv file called adm{Calibration_method}_NationalStat. The .csv file should have one column named adm{Calibration_method}_name and then one per crop-type named consistently with MAPSPAM's naming convention. Under each adm_name column there is the name of the region/district and each row has the total value of harvsted area (in hectares) per crop. It is important that the totals for each crop match national statistics in the FAOSTAT_HarvestedArea.csv file. Please check that the totals are consistent for each crop before running the notebook. 

---
# User Settings 
The first cell in BaseMap.ipynb is called *User Settings*. There the user will have to specify: 

* Country_name: this has to be consistent with the country_name used in your input data. Make sure that you are consistent with the "Country_name" in the input data files and the one used for running the notebook. We recommend using the the ISO 3166-1 alpha-3 codes that you can find here: https://www.iso.org/obp/ui/#search/code/ 

* FAOSTAT_country_name: In the FAOSTAT_HarvestedArea2023.csv file, there is a column called *Area* where there are listed all countries covered by FAOSTAT. If you are unsure about the naming convention, look for your country of interest and write its name in FAOSTAT_country_name. 

* cropland_value : this represents the value used for cropland in your land cover raster. You can use more than one value written as tuple (e.g. (10, 11, 20))

* Calibration_method: the calibration method refers to the administrative level at which the calibration is done (adm 0, 1 or 2). The test folder performs it at adm2. If the calibration method is greater than 0, then the statistics are not oprional.

* SSP and Climate_model : these data are needed for downloading crop water demand from the Global Agro Ecolgical Zone (GAEZ) dataset v5. See more details on the notebook. 

* Coordinate and projection systems: use projected coordinate reference system (projected CRS in meters).


## Lookup Tables

### Crop Correspondence Table

When using different datasets (in this case from MAPSPAM and FAOSTAT), the naming convention can vary. This .csv file is needed for bridging this gap. The Example refers to the .csv file in the test folder. Under the column "Crop" there are the crop names from the national statistics, while the column "MAPSPAM" contains MAPSPAM's names. This .csv file has to be created manually by the user. 


Example:

| Crop       | MAPSPAM      |
| ---------- | ------------ |
| Maize      | MAIZ         |
| Millet     | MILL         |



## Test

Before running the notebook, check the test folder available on Zenodo https://zenodo.org/records/23882328. The content can be copy-pasted into the Data/00.InputData folder to test the worklow.

---

# Workflow Steps

## Step 1 – Create Base Cropland Layer
This serves as a base layer for the analysis. It is used as a proxy for downscaling crops' harvested area statistics and faming-system's data available at higher resolution.
### Processing

* Reproject land cover 
* Reclassify the land cover raster
* Convert crop-specific rasters to binary rasters:

  * Crop = 1
  * Other classes = 0

* Convert rasters to point layers

### Output

```text
01.BaseCroplandLayer/
```

Files:

```text
{Country_name}_reclassified.tif
{Country_name}_reclassified_nod.tif  --> in the bolean created, the zeros here become NoData
{Country_name}.gpkg
```
Where the .gpkg file represents the final point vector with cropland. 

---

## Step 2 – Add Farmland Information
Open Street Map provides shapefiles of farmlands. This is used as a proxy for high-input crops. Before running this step on BaseMap.ipynb, run **FarmlandClassification.ipynb** and follow the instructions there to create crop classes with the same naming convention as the IFPRI dataset. 
### Processing

* Add X and Y coordinates to each point
* Overlay OSM farmland polygons
* Create a farmland indicator column with the Farmland classes created in the FarmlandClassification.ipynb

### Output

```text
01.BaseCroplandLayer/{Country_name}_crp_farmland.gpkg
```


---

## Step 3 – Process MAPSPAM Crop Rasters
The rasters are clipped and reprojected according to the case-study country specifics. 
### Processing

#### 3.1 Clip Rasters

* Clip all MAPSPAM crop rasters to the country boundary
* Preserve NoData values

Output:

```text
02.{Country_name}_MAPSPAM_Crops/
└── 01.{Country_name}_Clipped_Rasters/
```

#### 3.2 Reproject Rasters

* Reproject clipped rasters to the coordinate system consisten with the land cover

Output:

```text
02.{Country_name}_MAPSPAM_Crops/
└── 01.{Country_name}_Clipped_Rasters/
└── 02.Reprojected_Rasters/
```

## Step 4 – Calculate the share of irrigated area and remove empty rasters 
MAPSPAM provides raster datasets for all crops available globally. However, not all crops can be harvested in every country. For each case-study country, empty clipped rasters are filtered out to retain only crops that are currently grown there. A final CSV file containing summary statistics is then generated to estimate the share of irrigated area.

For each crop MAPSPAM provides:

* I = Irrigated harvested area
* R = Rainfed harvested area
* A = Total harvested area

Where:

```text
A = I + R
```

Calculate:

```text
Share_I = I / A
```
Where Share_I >= 0.95 is set to 1, Share_I < 0.05 is set to 0,
otherwise rounded to 2 decimals.
We create a MAPSPAMsummary_stat.csv file with MAPSPAM summary statistics. The statistics are structured as crop name, I, A, Share_I

If a crop has A=0, then it means that the crop is not growing in the country. For every crop name that has A>0, we keep the I and R rasters from the folder 02.Reprojected_Rasters and save them into the third folder:

```text
02.{Country_name}_MAPSPAM_Crops/
└── 01.{Country_name}_Clipped_Rasters/
└── 02.Reprojected_Rasters/
└── 03.{Country_name}_Filtered_Rasters/
```



## Step 5 – Generate Density Rasters

The density rasters are used for downscaling the harvested area to the higher-resolution cropland point layer. Using total harvested area directly would assign the same value to every point within a raster cell. To avoid this, harvested area is converted to a density value, calculated as:

```text
Density = Harvested Area / Cell Area
```

Generated for:

* Irrigated area (I)
* Rainfed area (R)

Output:

```text
02.{Country_name}_MAPSPAM_Crops/
└── 01.{Country_name}_Clipped_Rasters/
└── 02.Reprojected_Rasters/
└── 03.{Country_name}_Filtered_Rasters/
└── 04.Density_Rasters/
```

---
## Step 6 – Resample the rasters to the cropland point layer.

The density crop rasters are joined to the cropland point layer. Every column is consistent withe raster names (and therefore MAPSPAM naming convention)

Output vector layer ({Country_name}_crop_IR.gpkg):

| id | x | y | farms | MAIZ_I | MAIZ_R | WHEA_I | WHEA_R |
| -- | - | - | ------| ------ | ------ | ------ | ------ |

Output folder:

```text
03.Final/
└── 01.{Country_name}_crop_IR/
```
If the calibration is done on a sub-national level (adm1 and 2), then the crop_IR.gpkg also carries the "adm" column, specifying the name region/district. The names are consistent with the ones found in {Country_name}_admin1.shp or {Country_name}_admin2.shp
---


## Step 7 – Calibrate cropland

National or sub-national statistics are used to calibrate harvested areas. The initial crop density dataset is provided at 10 km resolution. Resampling to a higher spatial resolution may introduce some loss of information. This calibration step ensures that the final rasterized crop areas match national-level crop statistics.

## Processing

### 1. Calculate harvested area per point

For each point in `{Country_name}_crop_IR.gpkg` located in `03.Final/01.{Country_name}.crop_IR`, the harvested area is calculated as:

```text
Harvested_Area = Density × Point_Area
```

where `Point_Area` is the original pixel area of `{Country_name}_LandCover.tif` located in `Data/00.InputData`.

### 2. Calculate national harvested area from MAPSPAM

For each crop, all values associated with `_I`  and `_R` are summed separately to obtain the total harvested area at the national scale (consistently with calibration_method).

The results are saved as:

* `Sum_MAPSPAM_I.csv`
* `Sum_MAPSPAM_R.csv`


in `03.Final/02.Calibration`.

Each file contains three columns:

* `adm`
* `Crop`
* `Harvested_Area (ha)`

### 3. Harmonize crop names

The file `CropNames.csv` is read to harmonize crop names between the MAPSPAM and FAOSTAT naming conventions.

A new csv file is created from `FAOSTAT_HarvestedArea2023.csv` using MAPSPAM crop names and the following structure:

```text
MAPSPAM_Crop, National_Area (ha)
```
The new .csv file is called `National_harvested_{Country_name}.csv`
For example, all records corresponding to maize in the FAOSTAT dataset (e.g. "Maize (corn)") are aggregated and assigned to the MAPSPAM crop code `MAIZ`.

### 4. Create calibrated irrigated crop totals

The file `Data/{Country_name}_MAPSPAM_crops/MAPSPAMsummary-stat.csv` is read.

For each crop, the column `Share_I` is used to estimate the irrigated harvested area. Only irrigated crops (i.e. crops ending with `_I` in `{Country_name}_crop_IR.gpkg`) are retained.

The irrigated national harvested area is calculated as:

```text
Calibrated_Irrigated_Area = Share_I × National_Area
```

for all crops where `Share_I > 0`.

The results are saved in:

```text
calibrated_irrigated_crops.csv
```

### 5. Create calibrated rainfed crop totals

For crops that contain both irrigated (`_I`) and rainfed (`_R`) classes, the rainfed harvested area is calculated as:

```text
Calibrated_Rainfed_Area = National_Area − Calibrated_Irrigated_Area
```

If `Share_I = 0`, the entire harvested area is assigned to the rainfed crop:

```text
Calibrated_Rainfed_Area = National_Area
```

The results are saved in:

```text
calibrated_rainfed_crops.csv
```

### 6. Output format

The files:

* `calibrated_irrigated_crops.csv`
* `calibrated_rainfed_crops.csv`

must have the same format as:

* `Sum_MAPSPAM_I.csv`
* `Sum_MAPSPAM_R.csv`

respectively.

### 7. Calculate scaling factors

For each crop, scaling factors are calculated as:

```text
Scaling_Factor_I = National_Area(per crop) / Calculated_Area(from MAPSPAM per crop)

where:
National_Area(per crop) is obtained from calibrated_irrigated_crops.csv
Calculated_Area(from MAPSPAM per crop) is obtained from Sum_MAPSPAM_I.csv
```

```text
Scaling_Factor_R = National_Area(per crop) / Calculated_Area(from MAPSPAM per crop)

where:
National_Area(per crop) is obtained from calibrated_rainfed_crops.csv
Calculated_Area(from MAPSPAM per crop) is obtained from Sum_MAPSPAM_R.csv
```

All scaling factors are saved in:

```text
Scaling_factors.csv
```

The file must contain all crop names listed in the `farms` column of `{Country_name}_crop_IR_filtered`.


### 8. Apply scaling factors

A new GeoPackage is created by multiplying each crop value in `{Country_name}_crop_IR.gpkg` by its corresponding scaling factor.

The output is saved as:

```text
{Country_name}_crop_IR_calibrated1.gpkg
```

### 9. Redistribute harvested area densities exceeding the available area

In `{Country_name}_crop_IR_calibrated1.gpkg`, each crop column contains harvested-area density values. Because each row represents the area of one land-cover pixel, the sum of crop densities within a row cannot exceed 1.

The total density at each point is calculated as:

```text
Total_Density = Sum(All_Crop_Densities)
```

Points with:

```text
Total_Density > 1
```

are considered overloaded.

The excess density is calculated as:

```text
Excess_Density = Total_Density − 1
```

For each overloaded point, the proportional contribution of each crop is calculated as:

```text
Crop_Proportion = Crop_Density / Total_Density
```

The available capacity of each potential receiving point is calculated as:

```text
Available_Capacity = 1 − Total_Density
```

Only points with:

```text
Available_Capacity > 0
```

are eligible receivers. Nerby points are found with a KD-tree. 
The poiints are split by the adm column when Calibration_method is 1 or 2, so each district (or province) is handled on its own. With method 0 the whole country is one group.
A cKDTree is built from the point coordinates of that group only. Excess density can therefore never cross a district border.

The amount of density transferred from an overloaded point to a receiving point is:

```text
Transferred_Density = Minimum(Excess_Density, Available_Capacity)
```

The transferred density is distributed proportionally among crops:

```text
Receiving_Crop_Density(New) =
Receiving_Crop_Density(Old) +
Transferred_Density × Crop_Proportion
```

```text
Source_Crop_Density(New) =
Source_Crop_Density(Old) -
Transferred_Density × Crop_Proportion
```

Overloaded points are processed in descending order of excess density. Redistribution continues iteratively until all points satisfy:

```text
Total_Density ≤ 1
```

The excess is done in two phases: 
* Phase 1: Irrigated points move first. This means that if irrigation expanded, all the points with mixed irrigated-rainfed values become fully irrigated, assuming that irrigation expanded from points already irrigated. 
* Phase 2: Once irrigated crops match national statistics, then only rainfed move. 

The final output has calibrated crops with values for harvested area in hectared and it is saved as:

```text
{Country_name}_crop_IR_calibrated2.gpkg
```

### Note for sub-national scale

If the calibration is done at sub-national level, then the workflow creates one scaling factor per crop and adm. If some crops are not in the statistics but in the FAOSTAT file, then the scaling factor is calculated as before (one per crop at a national scale).

The calibration and re-distirbution is done at sub-national level. If one crop is missing from GIS data, but reported in sub-national statistics, then the corresponding share is distributed equally across all cropland points available in that sub-national area. 



---

## Step 8 – Assign Farming Systems

### High-Input Systems (commercial farming)

If a point in the {Country_name}_crop_IR_calibrated2.gpkg overlaps an OSM farmland crop_class, then it is classified as high-input system:

* High-input irrigated (HI)
* High-input rainfed (HR)

Example: a point that has SUGC_HI in farms, then th corresponding harvested are is the one of  SUGC in that row. All
other crops at that point stay LI/LR

### Low-Input Systems (traditional farming)

All remaining areas are assigned to:

* Low-input irrigated (LI)
* Low-input rainfed (LR)

Final output table format:

| id | x | y | MAIZ_HI | MAIZ_HR | MAIZ_LI| MAIZ_LR |
| -- | - | - | --------| ------- | ------ | ------- |

---
## Step 9 – Add crop water demand.

This final step estimates crop-specific water demand for irrigated crops. Crop water deficit data are sourced from GAEZ v5. The dataset is provided in mm at approximately 10 km spatial resolution. Depending on user settings, the dataset can be sourced from different climate models. The final output is a GeoPackage file with crop specific water requirements in Bcm over hectares. 


Final output table format:

| id | x | y | MAIZ_HI | MAIZ_HR | cwd_MAIZ_LI| cwd_MAIZ_LR |
| -- | - | - | --------| ------- | ---------- | ----------- |

---

# Output Structure 

```text
Data/
└── 00.InputData/
└── 01.BaseCroplandLayer/
└── 02.{Country_name}_MAPSPAM_Crops/
    └── 01.{Country_name}_Clipped_Rasters/
    └── 02.Reprojected_Rasters/
    └── 03.{Country_name}_Filtered_Rasters/
    └── 04.Density_Rasters/
└── 03.Final/
    └── 01.{Country_name}_Crop_IR/
    └── 02.Calibration/
└── 04.CropWaterDemand/
    └── 01.{Country_name}_Clipped_Rasters/
    └── 02.Reprojected_Rasters/

```

---


## License

MIT License

---

## Sources
International Food Policy Research Institute (IFPRI), 2026, "Global Spatially-Disaggregated Crop Production Statistics Data for 2020 Version 2.0 Release 2", https://doi.org/10.7910/DVN/SWPENT, Harvard Dataverse, V5

FAO & IIASA. 2025. Global Agro-ecological Zoning version 5 (GAEZ v5) Model Documentation. https://github.com/un-fao/gaezv5/wiki

FAO. 2024. Crops and livestock products: Area harvested. Accessed on 05 June 2026. https://www.fao.org/faostat/en/#data/QCL Licence: CC-BY-4.0.

---

## Acknowledgements
This material has been produced with support from the Climate Compatible Growth programme, which is funded by the UK Government’s Foreign, Commonwealth & Development Office (FCDO) through its Global Research and Technology Development portfolio. The views expressed are those of the authors and do not necessarily reflect the official policy or positions of the UK Government’s Foreign, Commonwealth & Development Office (FCDO) or its partners.
