from __future__ import absolute_import, division, print_function
from iotbx.phil import parse

#help_message = '''
#diffBragg command line utility
#'''

hopper_phil = """
use_float32 = False
  .type = bool
  .help = store pixel data and background models in 32bit arrays
  .expert_level=10
test_gathered_file = False
  .type = bool
  .help = run a quick test to ensure the gathered data file preserves information
  .expert_level=10
load_data_from_refls = False
  .type = bool
  .help = load image data, background etc from reflection tables
  .expert_level=10
gathered_output_file = None
  .type = str
  .help = optional file for storing a new hopper input file which points to the gathered data dumps
  .expert_level=10
only_dump_gathers = False
  .type = bool
  .help = only reads in image data, fits background planes, and dumps
  .help = results to disk, writes a new exper refl file at the end
  .expert_level=10
gathers_dir = None
  .type = str
  .help = folder where gathered data reflection tables
  .help = will be writen (if dump_gathers=True)
  .expert_level=10
dump_gathers = False
  .type = bool
  .help = optionally dump the loaded experimental data to reflection tables
  .help = for portability
  .expert_level=10
spectrum_from_imageset = False
  .type = bool
  .help = if True, load the spectrum from the imageset in the experiment, then probably downsample it
  .expert_level=0
isotropic {
  diffuse_gamma = False
    .type = bool
    .help = refine a single diffuse gamma parameter as opposed to 3
  diffuse_sigma = False
    .type = bool
    .help = refine a single diffuse gamma parameter as opposed to 3
}
downsamp_spec {
  skip = False
    .type = bool
    .help = if reading spectra from imageset, optionally skip the downsample portion
    .help = Note, if skip=True, then total flux will be determined by whats in the imageset spectrum (sum of the weights)
    .expert_level=10
  filt_freq = 0.07
    .type = float
    .help = low pass filter frequency in units of inverse spectrometer pixels (??)
    .expert_level=10
  filt_order = 3
    .type = int
    .help = order for bandpass butter filter
    .expert_level=10
  tail = 50
    .type = int
    .help = endpoints of the spectrum that are used in background estimation
    .expert_level=10
  delta_en = 0.5
    .type = float
    .help = final resolution of downsampled spectrum in eV
    .expert_level=0
}
apply_best_crystal_model = False
  .type = bool
  .help = depending on what experiments in the exper refl file, one may want
  .help = to apply the optimal crystal transformations (this parameter only matters
  .help = if params.best_pickle is not None)
  .expert_level=10
filter_unpredicted_refls_in_output = True
  .type = bool
  .help = filter reflections in the output refl table for which there was no model bragg peak
  .help = after stage 1 termination
  .expert_level=10
tag = stage1
  .type = str
  .help = output name tag
  .expert_level=0
ignore_existing = False
  .type = bool
  .help = experimental, ignore expts that already have optimized models in the output dir
  .expert_level=0
global_method = *basinhopping annealing
  .type = choice
  .help = the method of global optimization to use
  .expert_level=10
nelder_mead_maxfev = 60
  .type = int
  .help = multiplied by total number of modeled pixels to get max number of iterations
  .expert_level=10
nelder_mead_fatol = 0.0001
  .type = float
  .help = nelder mead functional error tolerance
niter_per_J = 1
  .type = int
  .help = if using gradient descent, compute gradients
  .help = every niter_per_J iterations .
  .expert_level=10
rescale_params = True
  .type = bool
  .help = use rescaled range parameters
  .expert_level=10
best_pickle = None
  .type = str
  .help = path to a pandas pickle containing the best models for the experiments
  .expert_level=0
betas
  .help = variances for the restraint targets
  .expert_level=0
{
  Nvol = 1e8
    .type = float
    .help = tightness of the Nabc volume contraint
  detz_shift = 1e8
    .type = float
    .help = restraint variance for detector shift target
  ucell = [1e8,1e8,1e8,1e8,1e8,1e8]
    .type = floats
    .help = beta values for unit cell constants
  RotXYZ = 1e8
    .type = float
    .help = restraint factor for the rotXYZ restraint
  Nabc = [1e8,1e8,1e8]
    .type = floats(size=3)
    .help = restraint factor for the ncells abc
  Ndef = [1e8,1e8,1e8]
    .type = floats(size=3)
    .help = restraint factor for the ncells def
  diffuse_sigma = 1e8,1e8,1e8
    .type = floats(size=3)
    .help = restraint factor for diffuse sigma
  diffuse_gamma = 1e8,1e8,1e8
    .type = floats(size=3)
    .help = restraint factor for diffuse gamma
  G = 1e8
    .type = float
    .help = restraint factor for the scale G
  B = 1e8
    .type = float
    .help = restraint factor for Bfactor
}
dual
  .help = configuration parameters for dual annealing
  .expert_level=10
{
  initial_temp = 5230
    .type = float
    .help = init temp for dual annealing
  no_local_search = False
    .type = bool
    .help = whether to try local search procedure with dual annealing
    .help = if False, then falls back on classical simulated annealing
  visit = 2.62
    .type = float
    .help = dual_annealing visit param, see scipy optimize docs
  accept = -5
    .type = float
    .help = dual_annealing accept param, see scipy optimize docs
}
centers
  .help = restraint targets
  .expert_level=0
{
  Nvol = None
    .type = float
    .help = if provided, constrain the product Na*Nb*Nc to this value
  detz_shift = 0
    .type = float
    .help = restraint target for detector shift along z-direction
  ucell = [63.66, 28.87, 35.86, 1.8425]
    .type = floats
    .help = centers for unit cell constants
  RotXYZ = [0,0,0]
    .type = floats(size=3)
    .help = restraint target for Umat rotations
  Nabc = [100,100,100]
    .type = floats(size=3)
    .help = restraint target for Nabc
  Ndef = [0,0,0]
    .type = floats(size=3)
    .help = restraint target for Ndef
  diffuse_sigma = [1,1,1]
    .type = floats(size=3)
    .help = restraint target for diffuse sigma
  diffuse_gamma = [1,1,1]
    .type = floats(size=3)
    .help = restraint target for diffuse gamma
  G = 100
    .type = float
    .help = restraint target for scale G
  B = 0
    .type = float
    .help = restraint target for Bfactor
}
skip = None
  .type = int
  .help = skip this many exp
  .expert_level=0
hess = None
  .type = str
  .help = scipy minimize hessian argument, 2-point, 3-point, cs, or None
  .expert_level=10
stepsize = 0.5
  .type = float
  .help = basinhopping stepsize
  .expert_level=10
temp = 1
  .type = float
  .help = temperature for basin hopping algo
  .expert_level=10
niter = 0
  .type = int
  .help = number of basin hopping iterations (0 just does a gradient descent and stops at the first minima encountered)
  .expert_level=0
exp_ref_spec_file = None
  .type = str
  .help = path to 3 col txt file containing file names for exper, refl, spectrum (.lam)
  .expert_level=0
method = None
  .type = str
  .help = minimizer method, usually this is L-BFGS-B (gradients) or Nelder-Mead (simplex)
  .help = other methods are experimental (see details in hopper_utils.py)
  .expert_level=0
opt_det = None
  .type = str
  .help = path to experiment with optimized detector model
  .expert_level=0
opt_beam = None
  .type = str
  .help = path to experiment with optimized beam model
  .expert_level=0
number_of_xtals = 1
  .type = int
  .help = number of crystal domains to model per shot
  .expert_level=10
sanity_test_input = True
  .type = bool
  .help = sanity test input
  .expert_level=10
outdir = None
  .type = str
  .help = output folder
  .expert_level=0
max_process = -1
  .type = int
  .help = max exp to process
  .expert_level=0
sigmas
  .help = sensitivity of target to parameter (experimental)
  .expert_level=10
{
  detz_shift = 1
    .type = float
    .help = sensitivity shift for the overall detector shift along z-direction
  Nabc = [1,1,1]
    .type = floats(size=3)
    .help = sensitivity for Nabc
  Ndef = [1,1,1]
    .type = floats(size=3)
    .help = sensitivity for Ndef
  diffuse_sigma = [1,1,1]
    .type = floats(size=3)
    .help = sensitivity for diffuse sigma
  diffuse_gamma = [1,1,1]
    .type = floats(size=3)
    .help = sensitivity for diffuse gamma
  RotXYZ = [1,1,1]
    .type = floats(size=3)
    .help = sensitivity for RotXYZ
  G = 1
    .type = float
    .help = sensitivity for scale factor
  B = 1
    .type = float
    .help = sensitivity for Bfactor
  ucell = [1,1,1,1,1,1]
    .type = floats
    .help = sensitivity for unit cell params
  Fhkl = 1
    .type = float
    .help = sensitivity for structure factors
}
init
  .help = initial value of model parameter (will be overrided if best pickle is provided)
  .expert_level=0
{
  detz_shift = 0
    .type = float
    .help = initial value for the detector position overall shift along z-direction in millimeters
  Nabc = [100,100,100]
    .type = floats(size=3)
    .help = init for Nabc
  Ndef = [0,0,0]
    .type = floats(size=3)
    .help = init for Ndef
  diffuse_sigma = [.01,.01,.01]
    .type = floats(size=3)
    .help = init diffuse sigma
  diffuse_gamma = [1,1,1]
    .type = floats(size=3)
    .help = init for diffuse gamma
  RotXYZ = [0,0,0]
    .type = floats(size=3)
    .help = init for RotXYZ
  G = 1
    .type = float
    .help = init for scale factor
  B = 0
    .type = float
    .help = init for B factor
}
mins
  .help = min value allowed for parameter
  .expert_level = 0
{
  detz_shift = -10
    .type = float
    .help = min value for detector z-shift in millimeters
  Nabc = [3,3,3]
    .type = floats(size=3)
    .help = min for Nabc
  Ndef = [-200,-200,-200]
    .type = floats(size=3)
    .help = min for Ndef
  diffuse_sigma = [0,0,0]
    .type = floats(size=3)
    .help = min diffuse sigma
  diffuse_gamma = [0,0,0]
    .type = floats(size=3)
    .help = min for diffuse gamma
  RotXYZ = [-1,-1,-1]
    .type = floats(size=3)
    .help = min for rotXYZ in degrees
  G = 0
    .type = float
    .help = min for scale G
  B = 0
    .type = float
    .help = min for Bfactor
  Fhkl = 0
    .type = float
    .help = min for structure factors
}
maxs
  .help = max value allowed for parameter
  .expert_level = 0
{
  detz_shift = 10
    .type = float
    .help = max value for detector z-shift in millimeters
  eta = 0.1
    .type = float
    .help = maximum mosaic spread in degrees
  Nabc = [300,300,300]
    .type = floats(size=3)
    .help = max for Nabc
  Ndef = [200,200,200]
    .type = floats(size=3)
    .help = max for Ndef
  diffuse_sigma = [20,20,20]
    .type = floats(size=3)
    .help = max diffuse sigma
  diffuse_gamma = [1000,1000,1000]
    .type = floats(size=3)
    .help = max for diffuse gamma
  RotXYZ = [1,1,1]
    .type = floats(size=3)
    .help = max for rotXYZ in degrees
  G = 1e12
    .type = float
    .help = max for scale G
  B = 1e3
    .type = float
    .help = max for Bfactor
  Fhkl = 1e6
    .type = float
    .help = max for structure factors
}
fix
  .help = flags for fixing parameters during refinement
  .expert_level = 0
{
  G = False
    .type = bool
    .help = fix the Bragg spot scale during refinement
  B = True
    .type = bool
    .help = fix the Bfactor during refinement
  RotXYZ = False
    .type = bool
    .help = fix the misorientation matrix during refinement
  Nabc = False
    .type = bool
    .help = fix the diagonal mosaic domain size parameters during refinement
  Ndef = False
    .type = bool
    .help = fix the diagonal mosaic domain size parameters during refinement
  diffuse_sigma = True
    .type = bool
    .help = fix diffuse sigma
  diffuse_gamma = True
    .type = bool
    .help = fix diffuse gamma
  ucell = False
    .type = bool
    .help = fix the unit cell during refinement
  detz_shift = False
    .type = bool
    .help = fix the detector distance shift during refinement
}
relative_tilt = False
  .type = bool
  .help = fit tilt coef relative to roi corner
  .expert_level = 10
num_mosaic_blocks = 1
  .type = int
  .help = number of mosaic blocks making up mosaic spread dist (not implemented)
  .expert_level = 10
ucell_edge_perc = 10
  .type = float
  .help = precentage for allowing ucell to fluctuate during refinement
  .expert_level = 10
ucell_ang_abs = 5
  .type = float
  .help = absolute angle deviation in degrees for unit cell angles to vary during refinement
  .expert_level = 10
no_Nabc_scale = False
  .type = bool
  .help = toggle Nabc scaling of the intensity
  .expert_level = 10
use_diffuse_models = False
  .type = bool
  .help = if True, let the values of init.diffuse_sigma and init.diffuse_gamma
  .help = be used to define the diffuse scattering. Set e.g. fix.diffuse_sigma=True in order to refine them
  .expert_level = 10
sigma_frac = None
  .type = float
  .help = sigma for Fhkl restraints will be some fraction of the starting value
  .expert_level = 10
sanity_test_hkl_variation = False
  .type = bool
  .help = measure the variation of each HKL within the shoebox
  .expert_level = 10
sanity_test_models = False
  .type = bool
  .help = make sure best models from stage 1 are reproduced at the start
  .expert_level = 10
sanity_test_amplitudes = False
  .type = bool
  .help = if True, then quickly run a sanity check ensuring that all h,k,l are predicted
  .help = and/or in the starting miller array
  .expert_level = 10
x_write_freq = 25
  .type = int
  .help = save x arrays every x_write_freq iterations
  .expert_level = 10
percentile_cut = None
  .type = float
  .help = percentile below which pixels are masked
  .expert_level = 10
space_group = None
  .type = str
  .help = space group to refine structure factors in
  .expert_level = 0
first_n = None
  .type = int
  .help = refine the first n shots only
  .expert_level = 0
maxiter = 15000
  .type = int
  .help = stop refiner after this many iters
  .expert_level = 10
ftol = 1e-10
  .type = float
  .help = ftol convergence threshold for scipys L-BFGS-B
  .expert_level = 10
disp = False
  .type = bool
  .help = scipy minimize convergence printouts
  .expert_level = 10
use_restraints = True
  .type = bool
  .help = disable the parameter restraints
  .expert_level = 0
min_multi = 2
  .type = int
  .help = minimum ASU multiplicity, obs that fall below this threshold
  .help = are removed from analysis
  .expert_level = 10
min_spot = 5
  .type = int
  .help = minimum spots on a shot in order to optimize that shot
  .expert_level = 10
logging
  .help = controls the logging module for hopper and stage_two
  .expert_level = 10
{
  disable = False
    .type = bool
    .help = turn off logging
  logfiles_level = low *normal high
    .type = choice
    .help = level of the main log when writing logfiles
  logfiles = False
    .type = bool
    .help = write log files in the outputdir
  rank0_level = low *normal high
    .type = choice
    .help = console log level for rank 0, ignored if logfiles=True
  other_ranks_level = *low normal high
    .type = choice
    .help = console log level for all ranks > 0, ignored if logfiles=True
  overwrite = True
    .type = bool
    .help = overwrite the existing logfiles
  logname = None
    .type = str
    .help = if logfiles=True, then write the log to this file, stored in the folder specified by outdir
    .help = if None, then defaults to main_stage1.log for hopper, main_pred.log for prediction, main_stage2.log for stage_two
}
profile = False
  .type = bool
  .help = profile the workhorse functions
  .expert_level = 0
profile_name = None
  .type = str
  .help = name of the output file that stores the line-by-line profile (written to folder specified by outdir)
  .help = if None, defaults to prof_stage1.log, prof_pred.log, prof_stage2.log for hopper, prediction, stage_two respectively
  .expert_level = 10
"""

simulator_phil = """
simulator {
  oversample = 0
    .type = int
    .help = pixel oversample rate (0 means auto-select)
  device_id = 0
    .type = int
    .help = device id for GPU simulation
  init_scale = 1
    .type = float
    .help = initial scale factor for this crystal simulation
  total_flux = 1e12
    .type = float
    .help = total photon flux for all energies
  crystal {
    ncells_abc = (10,10,10)
      .type = floats(size=3)
      .help = number of unit cells along each crystal axis making up a mosaic domain
    ncells_def = (0,0,0)
      .type = floats(size=3)
      .help = off-diagonal components for mosaic domain model (experimental)
    has_isotropic_ncells = False
      .type = bool
      .help = if True, ncells_abc are constrained to be the same values during refinement
    mosaicity = 0
      .type = float
      .help = mosaic spread in degrees
    anisotropic_mosaicity = None
      .type = floats
      .help = mosaic spread 3-tuple or 6-tuple specifying anisotropic mosaicity
    num_mosaicity_samples = 1
      .type = int
      .help = the number of mosaic domains to use when simulating mosaic spread
    mos_angles_per_axis = 10
      .type = int
      .help = if doing a uniform mosaicity sampling, use this many angles per rotation axis
    num_mos_axes = 10
      .type = int
      .help = number of sampled rot axes if doing a uniform mosaicity sampling
    mosaicity_method = 2
      .type = int
      .help = 1 or 2. 1 is random sampling, 2 is even sampling
    rotXYZ_ucell = None
      .type = floats(size=9)
      .help = three missetting angles (about X,Y,Z axes), followed by
      .help = unit cell parameters. The crystal will be rotated according to
      .help = the matrix RotX*RotY*RotZ, and then the unit cell will be updated
  }
  structure_factors {
    mtz_name = None
      .type = str
      .help = path to an MTZ file
    mtz_column = None
      .type = str
      .help = column in an MTZ file
    dmin = 1.5
      .type = float
      .help = minimum resolution for structure factor array
    dmax = 30
      .type = float
      .help = maximum resolution for structure factor array
    default_F = 0
      .type = float
      .help = default value for structure factor amps
  }
  spectrum {
    filename = None
      .type = str
      .help = a .lam file (precognition) for inputting wavelength spectra
    stride = 1
      .type = int
      .help = stride of the spectrum (e.g. set to 10 to keep every 10th value in the spectrum file data)
    filename_list = None
      .type = str
      .help = path to a file containing 1 .lam filename per line
  }
  beam {
    size_mm = 1
      .type = float
      .help = diameter of the beam in mm
  }
  detector {
    force_zero_thickness = False
      .type = bool
      .help = if True, then set sensor thickness to 0
  }
}
"""

refiner_phil = """
refiner {
  load_data_from_refl = False
    .type = bool
  test_gathered_file = False
    .type = bool
  gather_dir = None
    .type = str
    .help = optional dir for stashing loaded input data in refl files (mainly for tests/portability)
  break_signal = None
    .type = int
    .help = intended to be used to break out of a long refinement job prior to a timeout on a super computer
    .help = On summit, set this to 12 (SIGUSR2), at least thats what it was last I checked (July 2021)
  debug_pixel_panelfastslow = None
    .type = ints(size=3)
    .help = 3-tuple of panel ID, fast coord, slow coord. If set, show the state of diffBragg
    .help = for this pixel once refinement has finished
  res_ranges = None
    .type = str
    .help = resolution-defining strings, where each string is
    .help = is comma-separated substrings, formatted according to "%f-%f,%f-%f" where the first float
    .help = in each substr specifies the high-resolution for the refinement trial, and the second float
    .help = specifies the low-resolution for the refinement trial. Should be same length as max_calls
  mask = None
    .type = str
    .help = path to a dials mask flagging the trusted pixels
  force_symbol = None
    .type = str
    .help = a space group lookup symbol used to map input miller indices to ASU
  force_unit_cell = None
    .type = ints(size=6)
    .help = a unit cell tuple to use
  randomize_devices = True
    .type = bool
    .help = randomly select a device Id
  num_devices = 1
    .type = int
    .help = number of cuda devices on current node
  refine_Fcell = None
    .type = ints(size_min=1)
    .help = whether to refine the structure factor amplitudes
  refine_spot_scale = None
    .type = ints(size_min=1)
    .help = whether to refine the crystal scale factor
  max_calls = [100]
    .type = ints(size_min=1)
    .help = maximum number of calls for the refinement trial
  panel_group_file = None
    .type = str
    .help = a text file with 2 columns, the first column is the panel_id and the second
    .help = column is the panel_group_id. Panels geometries in the same group are refined together
  update_oversample_during_refinement = False
    .type = bool
    .help = whether to update the oversample parameter as ncells changes
  sigma_r = 3
    .type = float
    .help = standard deviation of the dark signal fluctuation
  adu_per_photon = 1
    .type = float
    .help = how many ADUs (detector units) equal 1 photon
  use_curvatures_threshold = 10
    .type = int
    .help = how many consecutiv positive curvature results before switching to curvature mode
  curvatures = False
    .type = bool
    .help = whether to try using curvatures
  start_with_curvatures = False
    .type = bool
    .help = whether to try using curvatures in the first iteration
  tradeps = 1e-2
    .type = float
    .help = LBFGS termination parameter  (smaller means minimize for longer)
  io {
    restart_file = None
      .type = str
      .help = output file for re-starting a simulation
    output_dir = None
      .type = str
      .help = optional output directory
  }
  quiet = False
    .type = bool
    .help = silence the refiner
  verbose = 0
    .type = int
    .help = verbosity level (0-10) for nanoBragg
  num_macro_cycles = 1
    .type = int
    .help = keep repeating the same refinement scheme over and over, this many times
  ncells_mask = *000 110 101 011 111
    .type = choice
    .help = a mask specifying which ncells parameters should be the same
    .help = e.g. 110 specifies Na and Nb are refined together as one parameter
  reference_geom = None
    .type = str
    .help = path to expt list file containing a detector model
  stage_two {
    use_nominal_hkl = True
      .type = bool
      .help = use the nominal hkl as a filter for Fhkl gradients
    save_model_freq = 50
      .type = int
      .help = save the model  after this many iterations
    save_Z_freq = 25
      .type = int
      .help = save Z-scores for all pixels after this many iterations
    min_multiplicity = 1
      .type = int
      .help = structure factors whose multiplicity falls below this value
      .help = will not be refined
    Fref_mtzname = None
      .type = str
      .help = path to a reference MTZ file. if passed, this is used solely to
      .help = observe the R-factor and CC between it and the Fobs being optimized
    Fref_mtzcol = "Famp(+),Famp(-)"
      .type = str
      .help = column in the mtz file containing the data
    d_min = 2
      .type = float
      .help = high res lim for binner
    d_max = 999
      .type = float
      .help = low res lim for binner
    n_bin = 10
      .type = int
      .help = number of binner bins
  }
}
"""

roi_phil = """
roi {
  cache_dir_only = False
    .type = bool
    .help = if True, create the cache folder , populate it with the roi data, then exit
  fit_tilt = False
    .type = bool
    .help = fit tilt plane, or else background is simply an offset
  force_negative_background_to_zero = False
    .type = bool
    .help = if True and the background model evaluates to a negative number
    .help = within an ROI, then force the background to be 0 for all pixels in that ROI
  background_threshold = 3.5
    .type = float
    .help = for determining background pixels
  pad_shoebox_for_background_estimation = None
    .type = int
    .help = shoebox_size specifies the dimenstion of the shoebox used during refinement
    .help = and this parameter is used to increase that shoebox_size only during the background
    .help = estimation stage
  shoebox_size = 10
    .type = int
    .help = roi box dimension
  deltaQ = None
    .type = float
    .help = roi dimension in inverse Angstrom, such that shoeboxes at wider angles are larger.
    .help = If this parameter is supplied, shoebox_size will be ignored.
  reject_edge_reflections = True
    .type = bool
    .help = whether to reject ROIs if they occur near the detector panel edge
  reject_roi_with_hotpix = True
    .type = bool
    .help = whether to reject an ROI if it has a bad pixel
  hotpixel_mask = None
    .type = str
    .help = path to a hotpixel mask (hot pixels set to True)
  panels = None
    .type = str
    .help = panel list for refinement as a string, e.g. "0-8,10,32-40" . The ranges are inclusive,
    .help = e.g. 0-8 means panels 0,1,2,3,4,5,6,7,8
  fit_tilt_using_weights = True
    .type = bool
    .help = if not using robust estimation for background, and instead using tilt plane fit,
    .help = then this parameter will toggle the use of weights. Weights are the estimated
    .help = pixel variance, incuding readout and shot noises.
  allow_overlapping_spots = False
    .type = bool
    .help = if True, then model overlapping spots
}
"""

preditions_phil = """
predictions {
  weak_fraction = 0.5
    .type = float
    .help = fraction of weak predictions to integrate
  threshold = 1e-3
    .type = float
    .help = value determining the cutoff for the forward model intensity. Bragg peaks will then be determined
    .help = as regions of connected values greater than the threshold
  oversample_override = None
    .type = int
    .help = force the pixel oversample rate to this value during the forward model simulation
    .help = for maximum speed gains, set this to 1, but inspect the output!
    .expert_level=10
  use_diffBragg_mtz = False
    .type = bool
    .help = whether to use the mtz supplied to diffBragg for prediction
  Nabc_override = None
    .type = ints(size=3)
    .help = use this value of mosaic block size for every shot, useful to get more predicted spots
    .expert_level=10
  pink_stride_override = None
    .type = int
    .help = if specified, stride through the spectrum according to this interval
  default_Famplitude = 1e3
    .type = float
    .help = default structure factor amplitude for every miller index
    .help = this creates a flat prediction model, where the magnitude is dependent on the distance to the Ewald sphere
  resolution_range = [1,999]
    .type = floats(size=2)
    .help = high-res to low-res limit for prediction model
  symbol_override = None
    .type = str
    .help = specify the space group symbol to use in diffBragg (e,g, P43212),
    .help = if None, then it will be pulled from the crystal model
  method = *diffbragg exascale
    .type = choice
    .help = engine used for computing the forward model
    .help = diffbragg offers CUDA support via the DIFFBRAGG_USE_CUDA=1 environment variable specification
    .help = or openmp support using the OMP_NUM_THREADS flag
    .help = The exascale only uses CUDA (will raise error if CUDA is not confugured)
}
"""

philz = simulator_phil + refiner_phil + roi_phil + preditions_phil
phil_scope = parse(philz)