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

The workflow consists of eight main steps:

1. Creating the base cropland layer. 
2. Adding farmland information.
3. Processing MAPSPAM's crop rasters. 
4. Calculating the share of irrigated area. 
5. Generate Density rasters.
6. Resample the rasters to the cropland point layer (created in step 1).
7. Cropland calibration. 
8. Assigning farming systems. 

For further information about each step, see the Wokflow section below.

All the input and output data are stored into the folder

```text
Data/
```

---

# Input Data

Create a folder in the Data one named:

```text
00.InputData/
```

The folder should contain:

## Spatial Data

* Country boundary shapefile: https://gadm.org/download_country.html
* Land cover raster
In the test folder we used Copernicus Global Land Cover dataset (2022), accessed through the QGIS Copernicus plugin; classification based on the UN FAO Land Cover Classification System (LCCS).
* OSM farmland layer, accessed through the QGIS OSM plugin.
* MAPSPAM crop rasters developed by the International Food Policy Research Institute (IFPRI): https://www.mapspam.info/data/ 


Note: make sure the country boundary shapefile has the same reference system as MAPSPAM (EPSG:4326 (WGS 84))

## Statistical Data

* National agricultural statistics from FAOSTAT: https://www.fao.org/faostat/en/#data/QCL

MAPSPAM data are calibrated for 2020. In our case-study country, harvested area estimates vary considerably between years due to differences in assumptions, sensing systems, climate hazards (e.g., droughts and floods), and statistical sources. As a result, harvested areas may increase or decrease over time. To minimize additional uncertainty and ensure methodological consistency, we used the same dataset (sourced from FAOSTA) used by the the IFPRI institute for downscaling raw data. The FAOSTAT database has been used for the final calibration of and crop spatial allocation in step 7. 

## The country name

Make sure that you are consistent with the "Country_name" in the input data files and the one used for running the notebook. We recommend using the the ISO 3166-1 alpha-3 codes that you can find here: https://www.iso.org/obp/ui/#search/code/ 


## Lookup Tables

### Crop Correspondence Table

A CSV file called CropNames.csv.
When using different datasets (in this case from MAPSPAM and FAOSTAT), the naming convention can vary. This .csv file is needed for bridging this gap. The Example refers to the .csv file in the test folder. Under the column "Crop" there are the crop names from the national statistics, while the column "MAPSPAM" contains MAPSPAM's names. This .csv file has to be created manually by the user. 


Example:

| Crop       | MAPSPAM      |
| ---------- | ------------ |
| Maize      | MAIZ         |
| Millet     | MILL         |

### Farmland Legend

OSM provides vector polygons with farmlands, specifying the crop-type and if it is irrigated or not. The polygon has to be reclassified by the user. The user has to create a {Country_name}Farmlands.csv file that serves as a legend for classifying high mechanized cropland. An example for the case study of Zambia can be found in the test folder. The .csv file has this format: 
| Farmland | Class |
| MAIZ_HI  | 1     |

Note that the farmland names have to follow the same naming convention of MAPSPAM.

## Test

Before running the notebook, check the test folder. The contenct can be copy-pasted into the Data/00.InputData folder to test the worklow.
---

# Workflow Steps

## Step 1 – Create Base Cropland Layer
This serves as a base layer for the analysis. It is used as a proxy for downscaling crops' harvested area statistics and faming-system's data available at higher resolution.
### Processing

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
Open Street Map provides shapefiles of farmlands. This is used as a proxy for high-input crops.
### Processing

* Add X and Y coordinates to each point
* Overlay OSM farmland polygons
* Create a farmland indicator column with the Farmland "Class numbers" consistent with the reclassification done from OSM data and the Farmland legend .csv file.

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

---


## Step 7 – Calibrate cropland

National statistics are used to calibrate harvested areas. The initial crop density dataset is provided at 10 km resolution. Resampling to a higher spatial resolution may introduce some loss of information. This calibration step ensures that the final rasterized crop areas match national-level crop statistics.

## Processing

### 1. Calculate harvested area per point

For each point in `{Country_name}_crop_IR.gpkg` located in `03.Final/01.{Country_name}.crop_IR`, the harvested area is calculated as:

```text
Harvested_Area = Density × Point_Area
```

where `Point_Area` is the original pixel area of `{Country_name}_LandCover.tif` located in `Data/00.InputData`.

### 2. Calculate national harvested area from MAPSPAM

For each crop, all values associated with `_I` and `_R` are summed separately to obtain the total harvested area at the national scale.

The results are saved as:

* `Sum_MAPSPAM_I.csv`
* `Sum_MAPSPAM_R.csv`

in `03.Final/02.Calibration`.

Each file contains two columns:

* `Crop`
* `Harvested_Area (ha)`

### 3. Harmonize crop names

The file `CropNames.csv` is read to harmonize crop names between the MAPSPAM and FAOSTAT naming conventions.

A new dataframe is created from `HarvestedArea{Country_name}.csv` using MAPSPAM crop names and the following structure:

```text
Crop, National_Area (ha)
```

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

are eligible receivers.

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

The final output has calibrated crops with values for harvested area in hectared and it is saved as:

```text
{Country_name}_crop_IR_calibrated2.gpkg
```



---

## Step 8 – Assign Farming Systems

### High-Input Systems (commercial farming)

If a point in the {Country_name}_crop_IR_calibrated2.gpkg overlaps an OSM farmland polygon:

* High-input irrigated (HI)
* High-input rainfed (HR)

OSM farmlands are prioritised because they were cross-checked against the High-Input Suitability Index, which indicated a high likelihood of commercial high-input farming.

### Low-Input Systems (traditional farming)

All remaining areas are assigned to:

* Low-input irrigated (LI)
* Low-input rainfed (LR)

Final output table format:

| id | x | y | MAIZ_HI | MAIZ_HR | MAIZ_LI| MAIZ_LR |
| -- | - | - | --------| ------- | ------ | ------- |

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

```

---


## License

MIT License
