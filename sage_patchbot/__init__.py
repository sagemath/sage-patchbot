import pkg_resources

try:
    __version__ = pkg_resources.get_distribution('sage_patchbot').version
except pkg_resources.DistributionNotFound:
    __version__ = '0.0.0'
