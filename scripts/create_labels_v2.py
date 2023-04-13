#!/bin/env python3

"""
This script creates labels that can be used to train deep learning model to
predict whether there will be tropical cyclones or not.
This is the second version of the script `create_labels.py`,
basically, it will:
    * Only work with ibtracs data.
    * Work with data generated by new extract python script.
    * No configuration file needed.
"""

import argparse
from datetime import datetime, timedelta
import glob
import os
import pandas as pd
from tqdm import tqdm
import xarray as xr


def parse_arguments(args=None):
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--best-track',
        dest='best_track',
        action='store',
        required=True,
        help=''' Path to ibtracs .csv file.
        ''')

    parser.add_argument(
        '--observations-dir',
        dest='observations_dir',
        action='store',
        required=True,
        help='''
            Path to directory contains all observation files .nc and .conf file.
            This is also the directory that the will contain the output csv file.
            The output file will be `tc_{lead_time}h_{basins}.csv`.
            ''')

    parser.add_argument(
        '--leadtime',
        dest='leadtime',
        default=0,
        type=int,
        help='The lead time to generate the data. Default is 0h.')

    parser.add_argument(
        '--keep-pre-existing-storms',
        dest='keep_pre_existing_storms',
        action='store_true',
        help='Whether the labels contains prexisting storms')

    return parser.parse_args(args)


def parse_date_from_nc_filename(filename: str):
    FMT = '%Y%m%d_%H_%M'
    filename, _ = os.path.splitext(os.path.basename(filename))
    datepart = '_'.join(filename.split('_')[1:])
    return datetime.strptime(datepart, FMT)


def list_reanalysis_files(path: str) -> pd.DataFrame:
    files = glob.iglob(os.path.join(path, '*.nc'))
    files = ((parse_date_from_nc_filename(f), f) for f in files)
    dates, filepaths = zip(*files)
    return pd.DataFrame({
        'Date': dates,
        'Path': filepaths
    })


def load_best_track(
        path: str,
        domain: tuple[float, float, float, float]) -> tuple[pd.DataFrame, pd.DataFrame]:
    def filter_by_domain(df: pd.DataFrame):
        latmin, latmax, lonmin, lonmax = domain
        lon_mask = (df['LON'] >= lonmin) & (df['LON'] <= lonmax)
        lat_mask = (df['LAT'] >= latmin) & (df['LAT'] <= latmax)
        return df[lon_mask & lat_mask]

    df = pd.read_csv(path, skiprows=(1,), na_filter=False)
    # Parse data column.
    df['Date'] = pd.to_datetime(
        df['ISO_TIME'], format='%Y-%m-%d %H:%M:%S')

    # Convert Longitude.
    # TODO: check this.
    df['LON'] = df['LON'].apply(lambda l: l if l > 0 else 360 + l)

    # Group by SID, and only retain the first row.
    genesis_df = df.groupby('SID', sort=False).first()
    genesis_df = genesis_df.copy()
    genesis_df['SID'] = genesis_df.index

    return filter_by_domain(genesis_df), filter_by_domain(df)


def create_labels(file_genesis_df, best_track_df) -> pd.DataFrame:
    def has_genesis(row: pd.Series):
        # print(row.notna()['SID'])
        return row.notna()['SID']

    def last_observed(row: pd.Series):
        sid = best_track_df['SID']
        df = best_track_df[sid == row['SID']]
        # print(df, row)
        return df['Date'].iloc[-1]

    def will_develop_to_tc(row: pd.Series):
        mask = best_track_df['SID'] == row['SID']
        df = best_track_df[mask]
        return 'TS' in df['NATURE'].values

    def develop_to_tc_date(row: pd.Series):
        if will_develop_to_tc(row):
            mask = best_track_df['SID'] == row['SID']
            df = best_track_df[mask]
            ts_mask = df['NATURE'] == 'TS'
            return df[ts_mask]['Date'].iloc[0]
        
        return None

    def other_tc(row: pd.Series):
        original_file_date = row['OriginalDate']
        date_mask = best_track_df['Date'] == original_file_date
        not_current_genesis = best_track_df['SID'] != row['SID']
        df = best_track_df[date_mask & not_current_genesis]
        return df

    rows = []
    for _, row in tqdm(file_genesis_df.iterrows(), total=len(file_genesis_df)):
        is_genesis = has_genesis(row)
        other_tc_df = other_tc(row)
        other_tc_locations = zip(
            other_tc_df['LAT'].tolist(),
            other_tc_df['LON'].tolist())
        other_tc_locations = list(other_tc_locations)

        if is_genesis:
            label = {
                'Date': row['Date'],
                'Genesis': is_genesis,
                'TC': is_genesis,
                'TC Id': row['SID'],
                'Longitude': row['LON'],
                'Latitude': row['LAT'],
                'First Observed': row['Date'],
                'Last Observed': last_observed(row),
                'First Observed Type': row['NATURE'],
                'Will Develop to TC': will_develop_to_tc(row),
                'Developing Date': develop_to_tc_date(row),
                'Is Other TC Happening': len(other_tc_df) > 0,
                'Other TC Locations': other_tc_locations,
                'Path': row['Path'],
            }
        else:
            label = {
                'Date': row['Date'],
                'Genesis': is_genesis,
                'TC': is_genesis,
                'Is Other TC Happening': len(other_tc_df) > 0,
                'Other TC Locations': other_tc_locations,
                'Path': row['Path'],
            }

        rows.append(label)

    return pd.DataFrame(rows)


def get_domain(path: str) -> tuple[float, float, float, float]:
    ds = xr.load_dataset(path)
    lat = ds['lat'].values
    lon = ds['lon'].values
    return lat.min(), lat.max(), lon.min(), lon.max()


def main(args=None):
    args = parse_arguments(args)

    # List reanalysis files.
    files_df = list_reanalysis_files(args.observations_dir)
    files_df['OriginalDate'] = files_df['Date'].copy()
    files_df['Date'] = files_df['Date'].apply(
        lambda d: d + timedelta(hours=args.leadtime))

    files_df = files_df.sort_values('Date')

    # Get the domain of this dataset by loading the first file.
    domain = get_domain(files_df['Path'].iloc[0])

    genesis_df, best_track_df = load_best_track(args.best_track, domain)

    # Merge these two dataframes.
    files_genesis_df = pd.merge(files_df, genesis_df, how='left', on='Date')
    assert len(files_genesis_df) >= len(files_df), 'Just to make sure we dont remove rows from files_df'

    labels_df = create_labels(files_genesis_df, best_track_df)
    if not args.keep_pre_existing_storms:
        has_preexisting_storms = labels_df['Is Other TC Happening']
        has_genesis = labels_df['Genesis']
        labels_df = labels_df[has_genesis | ~has_preexisting_storms]

    output_path = os.path.join(
        args.observations_dir, f'tc_{args.leadtime}h.csv')
    labels_df.to_csv(output_path, index=False)

if __name__ == '__main__':
    main()
