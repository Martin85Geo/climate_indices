import argparse
from datetime import datetime
import indices
from ingest import ingest_nclimgrid
import logging
import math
import multiprocessing
import netCDF4
import netcdf_utils
import numpy as np
import numba
import random

#-----------------------------------------------------------------------------------------------------------------------
# set up matplotlib to use the Agg backend, in order to remove any dependencies on an X server
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

#-----------------------------------------------------------------------------------------------------------------------
# set up a basic, global logger which will write to the console as standard error
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%Y-%m-%d  %H:%M:%S')
logger = logging.getLogger(__name__)

#-----------------------------------------------------------------------------------------------------------------------
# ignore warnings
import warnings
warnings.simplefilter('ignore', Warning)

#-----------------------------------------------------------------------------------------------------------------------
# static constants
_VALID_MIN = -10.0
_VALID_MAX = 10.0

#-----------------------------------------------------------------------------------------------------------------------
# multiprocessing lock we'll use to synchronize I/O writes to NetCDF files, one per each output file
lock = multiprocessing.Lock()

#-----------------------------------------------------------------------------------------------------------------------
#@numba.jit
def _variable_attributes(index_name,
                         months=None):

    """
    Finds correct variable attributes for climate indices that will be computed by this processor.
    
    :param index_name: name of index for which attributes are requested
    :param months: for month-scaled indices a number of months to use as scale
    :return: dictionary of attribute names to values 
    """
    if index_name == 'pet':
          
        variable_name = 'pet'
        variable_attributes = {'standard_name': 'pet',
                               'long_name': 'Potential Evapotranspiration (PET), from Thornthwaite\'s equation',
                               'valid_min': 0.0,
                               'valid_max': 2000.0,
                               'units': 'millimeter'}
      
    elif index_name == 'pdsi':
          
        variable_name = 'pdsi'
        variable_attributes = {'standard_name': 'pdsi',
                               'long_name': 'Palmer Drought Severity Index (PDSI)',
                               'valid_min': -10.0,
                               'valid_max': 10.0}
      
    elif index_name == 'scpdsi':
          
        variable_name = 'scpdsi'
        variable_attributes = {'standard_name': 'scpdsi',
                               'long_name': 'Self-calibrated Palmer Drought Severity Index (PDSI)',
                               'valid_min': -10.0,
                               'valid_max': 10.0}
      
    elif index_name == 'phdi':
          
        variable_name = 'phdi'
        variable_attributes = {'standard_name': 'phdi',
                               'long_name': 'Palmer Hydrological Drought Index (PHDI)',
                               'valid_min': -10.0,
                               'valid_max': 10.0}
      
    elif index_name == 'pmdi':
          
        variable_name = 'pmdi'
        variable_attributes = {'standard_name': 'pmdi',
                               'long_name': 'Palmer Modified Drought Index (PMDI)',
                               'valid_min': -10.0,
                               'valid_max': 10.0}
      
    elif index_name == 'zindex':
          
        variable_name = 'zindex'
        variable_attributes = {'standard_name': 'zindex',
                               'long_name': 'Palmer Z-Index',
                               'valid_min': -10.0,
                               'valid_max': 10.0}

    else:

        # use the scale months in the variable name        
        variable_name = index_name + '_{}'.format(str(months).zfill(2))
    
        if index_name == 'pnp':
        
            variable_attributes = {'standard_name': variable_name,
                                   'long_name': 'Percent average precipitation, {}-month scale'.format(months),
                                   'valid_min': 0,
                                   'valid_max': 10.0,
                                   'units': 'percent of average'}

        elif index_name == 'spi_gamma':
        
            variable_attributes = {'standard_name': variable_name,
                                   'long_name': 'SPI (Gamma), {}-month scale'.format(months),
                                   'valid_min': -3.09,
                                   'valid_max': 3.09}
        
        elif index_name == 'spi_pearson':
        
            variable_attributes = {'standard_name': variable_name,
                                   'long_name': 'SPI (Pearson), {}-month scale'.format(months),
                                   'valid_min': -3.09,
                                   'valid_max': 3.09}
        
        elif index_name == 'spei_gamma':
        
            variable_attributes = {'standard_name': variable_name,
                                   'long_name': 'SPEI (Gamma), {}-month scale'.format(months),
                                   'valid_min': -3.09,
                                   'valid_max': 3.09}
        
        elif index_name == 'spei_pearson':
        
            variable_attributes = {'standard_name': variable_name,
                                   'long_name': 'SPEI (Pearson), {}-month scale'.format(months),
                                   'valid_min': -3.09,
                                   'valid_max': 3.09}

        else:
        
            message = '{0} is an unsupported index type'.format(index_name)
            logger.error(message)
            raise ValueError(message)

    return variable_attributes
    
#-----------------------------------------------------------------------------------------------------------------------
@numba.vectorize([numba.float64(numba.float64),
                  numba.float32(numba.float32)])
def _f2c(t):
    '''
    Converts a temperature value from Fahrenheit to Celsius
    '''
    return (t-32)*5.0/9

#-----------------------------------------------------------------------------------------------------------------------
def _rmse(predictions, targets):
    """
    Root mean square error
    
    :param predictions: np.ndarray
    :param targets: np.ndarray
    :return: np.ndarray
    """
    return np.sqrt(((predictions - targets) ** 2).mean())

#-----------------------------------------------------------------------------------------------------------------------
def _plot_and_save_histogram(difference_values,
                             number_of_bins,
                             range_lower, 
                             range_upper,
                             index_name,
                             grid_name,
                             title,
                             output_filepath):
    
    # plot a histogram of the differences
    plt.gcf().clear()
    plt.hist(difference_values[:], bins=number_of_bins, range=(range_lower, range_upper))
    plt.title(title + ': {0}, {1}'.format(index_name, grid_name))
    plt.xlabel("Value")
    plt.ylabel("Frequency")
    
    # save to file
    logger.info('Saving histogram plot for index %s to file %s', index_name, output_filepath)
    plt.savefig(output_filepath)

#-----------------------------------------------------------------------------------------------------------------------
def _plot_and_save_lines(expected,
                         actual,
                         difference_values,
                         grid_name,
                         varname,
                         output_filepath):

    # get the RMSE for the two sets of values
    error = _rmse(actual, expected)
    
    # set figure size to (x, y)
    plt.figure(figsize=(30, 6))
    
    # plot the values and differences
    x = np.arange(difference_values.size)
    ax = plt.axes()
    ax.set_ylim([-5, 5])
    plt.axhline()
    expected_line, = plt.plot(x, expected, color='blue', label='NCEI (expected)')
    actual_line, = plt.plot(x, actual, color='yellow', linestyle='--', label='NIDIS (actual)')
    diffs_line, = plt.plot(x, difference_values, color='red', label='Difference')
    plt.legend(handles=[expected_line, actual_line, diffs_line], loc='upper left')
    plt.title('Comparison for {0}: {1}     (RMSE: {2})'.format(grid_name, varname, error))
    plt.xlabel("months")
    plt.ylabel("value")
    
    plt.subplots_adjust(left=0.02, right=0.99, top=0.9, bottom=0.1)
    
    # save to file
    logger.info('Saving histogram plot for index %s to file %s', varname, output_filepath)
    plt.savefig(output_filepath, bbox_inches='tight')

#     plt.show()
    plt.close()

#-----------------------------------------------------------------------------------------------------------------------

if __name__ == '__main__':

    """
    This module is used to perform climate indices processing on nClimGrid datasets in NetCDF.
    """

    try:

        # log some timing info, used later for elapsed time
        start_datetime = datetime.now()
        logger.info("Start time:    %s", start_datetime)

        # parse the command line arguments
        parser = argparse.ArgumentParser()
        parser.add_argument("--grid", 
                            help="Valid values are \'nclimgrid\' and \prism\'", 
                            required=True)
        parser.add_argument("--source_dir", 
                            help="Base directory under which are directories and files for precipitation and max/min/mean temperature", 
                            required=True)
        parser.add_argument("--output_dir", 
                            help="Directory under which the output NetCDF files will be written", 
                            required=True)
        args = parser.parse_args()

        # variable names used within the monthly NetCDF
        temp_var_name = 'tavg'
        precip_var_name = 'prcp'
        awc_var_name = 'awc'

        if args.grid == 'nclimgrid':

            # perform an ingest of the NCEI nClimGrid datasets for input (temperature  
            # and precipitation) plus soil constants (available water capacity)
            ingest_nclimgrid.ingest_to_netcdf(args.source_dir, args.output_dir)

        elif args.grid == 'prism':
            
            # perform an ingest of the NCEI nClimGrid datasets for input (temperature  
            # and precipitation) plus soil constants (available water capacity)
            ingest_prism.ingest_to_netcdf(args.source_dir, args.output_dir)
            
        # perform the processing
        grid_processor = GridProcessor(args.output_file_base,
                                       args.precip_file,
                                       args.temp_file,
                                       args.awc_file,
                                       args.precip_var_name,
                                       args.temp_var_name,
                                       args.awc_var_name,
                                       args.month_scales,
                                       args.calibration_start_year,
                                       args.calibration_end_year)
        grid_processor.run()

        # open the NetCDF files
        with netCDF4.Dataset(args.out_file, 'a') as dataset:

            # variable names for variables to diff from the two datasets
            comparison_arrays = {'PDSI': ('wrcc_pdsi', 'pdsi'),
                                 'PHDI': ('wrcc_phdi', 'phdi'),
                                 'PMDI': ('wrcc_pmdi', 'pmdi'),
                                 'Z-Index': ('cmb_zndx', 'zindex')}
            for index, var_names in comparison_arrays.items():
                    
                # TODO validate that the two variables exist, have compatible dimensions/units, etc., all of which is assumed below  pylint: disable=fixme

                logger.info('Computing differences on variable %s', index)
            
                # allocate an array for the differences for this variable
                diffs = {}
                
                size = dataset.variables['division'][:].size
                
                # common title for plots
                histogram_title = 'CMB vs. NIDIS: '
     
                for division_index, division_id in enumerate(dataset.variables['division'][:]):
                 
                    logger.info('Computing diffs for climate division ID: %s', division_id)
                    
                    # get the variable var_names for the month, mask the NaNs (data assumed to be in (division, time) dimension order)
                    data_CMB = np.ma.masked_invalid(dataset.variables[var_names[0]][division_index, :], copy=False)
                    data_NIDIS = np.ma.masked_invalid(dataset.variables[var_names[1]][division_index, :], copy=False)
             
                    # get the difference of the two
                    differences = data_CMB - data_NIDIS
                    diffs[division_index] = differences
     
                    # plot the differences as a histogram and save to file
                    _plot_and_save_histogram(differences,
                                             80,   # number_of_bins
                                             -2,   # lower range
                                             2,    # upper range
                                             index,
                                             division_id,
                                             histogram_title,
                                             'C:/home/data/nclimdiv/diffs_histogram_{0}_{1}.png'.format(var_names[1], division_id))
     
                    # plot and save line graphs showing correlation of values and differences
                    _plot_and_save_lines(data_NIDIS,
                                         data_CMB,
                                         differences,
                                         division_id,
                                         index,
                                         'C:/home/data/nclimdiv/diffs_line_{0}_{1}.png'.format(var_names[1], division_id))
                    
                    # add to the differences dictionary with this division ID key 
                    diffs[division_id] = differences
                    
                # make sure that the variable name isn't already in use
                diff_variable_name = 'diffs_' + index
                if diff_variable_name in dataset.variables.keys():

                    variable = dataset.variables[diff_variable_name]
                    
                else:
                    
                    # get the NetCDF datatype applicable to the data array we'll store in the variable
                    random_array = random.choice(list(diffs.values()))
                    netcdf_data_type = netcdf_utils.find_netcdf_datatype(random_array[0])
                    
                    # create the variable, set the attributes
                    variable = dataset.createVariable(diff_variable_name, 
                                                      netcdf_data_type, 
                                                      ('division', 'time',), 
                                                      fill_value=np.NaN)
#                     variable.setncatts(variable_attributes)
                
                # get the total number of time steps
                times_size = dataset.variables['time'][:].size
                
                # loop over each existing division and add the corresponding data array, if one was provided
                for division_index, division_id in enumerate(list(dataset.variables['division'][:])):
                    
                    # make sure we have a data array of monthly values for this division
                    if division_index in diffs.keys():
        
                        # make sure the array has the expected number of time steps 
                        data_array = diffs[division_index]
                        if data_array.size == times_size:
                        
                            # assign the array into the current division's slot in the variable
                            variable[division_index, :] = np.reshape(data_array, (1, times_size))
        
                        else:
        
                            logger.info('Unexpected size of data array for division index {0} -- '.format(division_index) + 
                                        'expected {0} time steps but the array contains {1}'.format(times_size, data_array.size))

        # report on the elapsed time
        end_datetime = datetime.now()
        logger.info("End time:      %s", end_datetime)
        elapsed = end_datetime - start_datetime
        logger.info("Elapsed time:  %s", elapsed)

    except Exception as ex:
        logger.exception('Failed to complete', exc_info=True)
        raise
