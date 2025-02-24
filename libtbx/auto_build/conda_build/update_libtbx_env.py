"""
Script to copy and update libtbx_env contents

Usage: libtbx.python update_libtbx_env.py
"""
from __future__ import absolute_import, division, print_function

import argparse
import os
import shutil
import sys

from libtbx.path import absolute_path

# =============================================================================
def get_prefix_dir():
  '''
  Function that returns $PREFIX or sys.prefix

  Parameters
  ----------
    None

  Returns
  -------
    path to prefix
  '''
  prefix_dir = os.getenv('PREFIX')
  if prefix_dir is None:
    prefix_dir = sys.prefix
  if sys.platform == 'darwin':
    if 'python.app' in prefix_dir:
      prefix_dir = prefix_dir.split('python.app')[0]
  elif sys.platform == 'win32':
    prefix_dir = os.path.join(prefix_dir, 'Library')

  return prefix_dir

# =============================================================================
def get_default_dir():
  '''
  Function that returns the default location of libtbx_env in an installation.
  The default location is ${PREFIX}/share/cctbx

  Parameters
  ----------
    None

  Returns
  -------
    path of the default location
  '''
  prefix_dir = get_prefix_dir()
  default_dir = os.path.join(prefix_dir, 'share', 'cctbx')

  return default_dir

# =============================================================================
def copy_libtbx_env(default_dir=None):
  '''
  Function that copies libtbx_env from $LIBTBX_BUILD to $PREFIX
  If $LIBTBX_BUILD is not set, no copy is done. If libtbx_env does not
  exist, an IOError is raised.

  Parameters
  ----------
    default_dir: str
      The directory to copy libtbx_env

  Returns
  -------
    path or None: if the file is copied, the newly created path is returned
  '''
  value = None
  if default_dir is None:
    default_dir = get_default_dir()
  if os.getenv('LIBTBX_BUILD') is not None:
    src = os.path.join(os.getenv('LIBTBX_BUILD'), 'libtbx_env')
    dst = os.path.join(default_dir, 'libtbx_env')
    if not os.path.isfile(src):
      raise IOError(
        'The "libtbx_env" file does not exist in {src}.'.format(src=src))
    if not os.path.exists(default_dir):
      # assumes that only the last level needs to be created.
      os.mkdir(default_dir)
    value = shutil.copy(src, dst)
  return value

# =============================================================================
def update_libtbx_env(default_dir=None):
  '''
  Function that updates libtbx_env so that modules can be loaded from
  standard locations in $PREFIX

  Parameters
  ----------
    default_dir: str
    The directory to copy libtbx_env

  Returns
  -------
    None
  '''

  # unset LIBTBX_BUILD and load libtbx_env from $PREFIX
  if os.getenv('LIBTBX_BUILD') is not None:
    del os.environ['LIBTBX_BUILD']
  import libtbx.load_env

  if default_dir is None:
    default_dir = get_default_dir()

  sys_prefix = get_prefix_dir()

  # basic path changes
  env = libtbx.env
  env.build_path = absolute_path(sys_prefix)
  env.set_derived_paths()
  env.exe_path = env.bin_path
  env.pythonpath = list()
  sys_executable = sys.executable
  if sys.platform == 'darwin' and 'python.app' not in sys_executable:
    sys_executable = os.path.join(sys_prefix, 'python.app', 'Contents', 'MacOS', 'python')
  env.python_exe = env.as_relocatable_path(sys_executable)
  env.no_bin_python = True
  site_packages_path = None
  for path in sys.path:
    if path.endswith('site-packages'):
      site_packages_path = env.as_relocatable_path(path)
      break
  relocatable_sys_prefix = env.as_relocatable_path(sys_prefix)
  env.repository_paths = [relocatable_sys_prefix, site_packages_path]
  env.scons_dist_path = relocatable_sys_prefix

  # libtbx.python dispatcher
  env._write_dispatcher_in_bin(
    source_file=env.python_exe,
    target_file='libtbx.python',
    source_is_python_exe=True)

  # update module locations
  if sys.platform == 'win32':
    sys_prefix = get_prefix_dir() # has an extra "Library"
    relocatable_sys_prefix = env.as_relocatable_path(
      os.path.join(sys_prefix, '..', 'Lib', 'site-packages'))
  for name in env.module_dict:
    module = env.module_dict[name]
    new_paths = [relocatable_sys_prefix, relocatable_sys_prefix]
    for path in sys.path:
      check_this_path = path.startswith(sys_prefix)
      if sys.platform == 'win32':
        check_this_path = path.lower().startswith(abs(relocatable_sys_prefix).lower())
      if check_this_path:
        new_path = os.path.join(path, name)
        if os.path.isdir(new_path):
          new_paths[0] = env.as_relocatable_path(new_path)
          new_paths[1] = env.as_relocatable_path(new_path + '_' + module.mate_suffix)
          break
        if module.name == 'boost' and os.path.isdir(os.path.join(path, 'boost_adaptbx')):
          new_paths[1] = env.as_relocatable_path(new_path + '_' + module.mate_suffix)
          break
        if module.name == 'annlib' and os.path.isdir(os.path.join(path, 'annlib_adaptbx')):
          new_paths[0] = None
          new_paths[1] = env.as_relocatable_path(new_path + '_' + module.mate_suffix)
          break
        if module.name == 'phaser_voyager' and os.path.isdir(os.path.join(path, 'New_Voyager')):
          new_paths[0] = env.as_relocatable_path(os.path.join(path, 'New_Voyager'))
          new_paths[1] = None
          break
    dist_paths = module.dist_paths
    for i, path in enumerate(dist_paths):
      if path is not None:
        module.dist_paths[i] = new_paths[i]
    env.module_dist_paths[name] = new_paths[0]
    if name == 'boost':
      env.module_dist_paths[name] = relocatable_sys_prefix / 'include'
    name_adaptbx = name + '_' + module.mate_suffix
    if name_adaptbx in env.module_dist_paths:
      env.module_dist_paths[name_adaptbx] = new_paths[1]

    if name == 'libtbx':
      env.path_utility = env.as_relocatable_path(
        os.path.join(abs(new_paths[0]), 'command_line', 'path_utility.py'))

  # update dispatchers
  env.reset_dispatcher_bookkeeping()
  env.write_python_and_show_path_duplicates()
  for module in env.module_list:
    module.process_command_line_directories()

  # repickle
  env.build_path = absolute_path(default_dir)
  env.installed = True
  env.pickle()

  return 0

# =============================================================================
def run():
  parser = argparse.ArgumentParser(description=__doc__,
    formatter_class=argparse.RawDescriptionHelpFormatter)

  # default location is ${PREFIX}/share/cctbx
  default_dir = get_default_dir()

  parser.add_argument(
    '--default-dir', '--default_dir', default=default_dir, type=str,
    help="""The new default for the location of libtbx_env. By default,
      the new location is ${PREFIX}/share/cctbx. This feature is not
      fully supported yet.""")

  namespace = parser.parse_args()

  copy_libtbx_env(default_dir=namespace.default_dir)
  update_libtbx_env(default_dir=namespace.default_dir)

  return 0

# =============================================================================
if __name__ == '__main__':
  sys.exit(run())
