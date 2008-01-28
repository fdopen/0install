"""
Integration with native distribution package managers.
@since: 0.28
"""

# Copyright (C) 2007, Thomas Leonard
# See the README file for details, or visit http://0install.net.

import os, re
from logging import warn, info
from zeroinstall.injector import namespaces, model
from zeroinstall.support import basedir

dotted_ints = '[0-9]+(\.[0-9]+)*'
version_regexp = '(%s)(-(pre|rc|post|)%s)*' % (dotted_ints, dotted_ints)

def try_cleanup_distro_version(version):
	"""Try to turn a distribution version string into one readable by Zero Install.
	We do this by stripping off anything we can't parse.
	@return: the part we understood, or None if we couldn't parse anything
	@rtype: str"""
	match = re.match(version_regexp, version)
	if match:
		return match.group(0)
	return None

class Distribution(object):
	"""Represents a distribution with which we can integrate.
	Sub-classes should specialise this to integrate with the package managers of
	particular distributions. This base class ignores the native package manager.
	@since: 0.28
	"""

	def get_package_info(self, package, factory):
		"""Get information about the given package.
		Add zero or more implementations using the factory (typically at most two
		will be added; the currently installed version and the latest available).
		@param package: package name (e.g. "gimp")
		@type package: str
		@param factory: function for creating new DistributionImplementation objects from IDs
		@type factory: str -> L{model.DistributionImplementation}
		"""
		return

class DebianDistribution(Distribution):
	def __init__(self, db_dir):
		self.db_dir = db_dir
		dpkg_status = db_dir + '/status'
		self.status_details = os.stat(self.db_dir + '/status')

		self.versions = {}
		self.cache_dir = basedir.save_cache_path(namespaces.config_site, namespaces.config_prog)

		try:
			self.load_cache()
		except Exception, ex:
			info("Failed to load dpkg cache (%s). Regenerating...", ex)
			try:
				self.generate_cache()
				self.load_cache()
			except Exception, ex:
				warn("Failed to regenerate dpkg cache: %s", ex)

	def load_cache(self):
		stream = file(self.cache_dir + '/dpkg-status.cache')

		for line in stream:
			if line == '\n':
				break
			name, value = line.split(': ')
			if name == 'mtime' and int(value) != int(self.status_details.st_mtime):
				raise Exception("Modification time of dpkg status file has changed")
			if name == 'size' and int(value) != self.status_details.st_size:
				raise Exception("Size of dpkg status file has changed")
		else:
			raise Exception('Invalid cache format (bad header)')
			
		versions = self.versions
		for line in stream:
			package, version = line[:-1].split('\t')
			versions[package] = version

	def generate_cache(self):
		cache = []

		for line in os.popen("dpkg-query -W"):
			package, version = line.split('\t', 1)
			if ':' in version:
				# Debian's 'epoch' system
				version = version.split(':', 1)[1]
			clean_version = try_cleanup_distro_version(version)
			if clean_version:
				cache.append('%s\t%s' % (package, clean_version))
			else:
				warn("Can't parse distribution version '%s' for package '%s'", version, package)

		cache.sort() 	# Might be useful later; currently we don't care
		
		import tempfile
		fd, tmpname = tempfile.mkstemp(prefix = 'dpkg-cache-tmp', dir = self.cache_dir)
		try:
			stream = os.fdopen(fd, 'wb')
			stream.write('mtime: %d\n' % int(self.status_details.st_mtime))
			stream.write('size: %d\n' % self.status_details.st_size)
			stream.write('\n')
			for line in cache:
				stream.write(line + '\n')
			stream.close()

			os.rename(tmpname, self.cache_dir + '/dpkg-status.cache')
		except:
			os.unlink(tmpname)
			raise

	def get_package_info(self, package, factory):
		try:
			version = self.versions[package]
		except KeyError:
			return

		impl = factory('package:deb:%s:%s' % (package, version)) 
		impl.version = model.parse_version(version)

class RPMDistribution(Distribution):
	cache_leaf = 'rpm-status.cache'
	
	def __init__(self, db_dir):
		self.db_dir = db_dir
		pkg_status = os.path.join(db_dir, 'Packages')
		self.status_details = os.stat(pkg_status)

		self.versions = {}
		self.cache_dir=basedir.save_cache_path(namespaces.config_site,
						       namespaces.config_prog)

		try:
			self.load_cache()
		except Exception, ex:
			info("Failed to load cache (%s). Regenerating...",
			     ex)
			try:
				self.generate_cache()
				self.load_cache()
			except Exception, ex:
				warn("Failed to regenerate cache: %s", ex)

	def load_cache(self):
		stream = file(os.path.join(self.cache_dir, self.cache_leaf))

		for line in stream:
			if line == '\n':
				break
			name, value = line.split(': ')
			if name == 'mtime' and (int(value) !=
					    int(self.status_details.st_mtime)):
				raise Exception("Modification time of rpm status file has changed")
			if name == 'size' and (int(value) !=
					       self.status_details.st_size):
				raise Exception("Size of rpm status file has changed")
		else:
			raise Exception('Invalid cache format (bad header)')
			
		versions = self.versions
		for line in stream:
			package, version = line[:-1].split('\t')
			versions[package] = version

	def __parse_rpm_name(self, line):
		"""Some samples we have to cope with (from SuSE 10.2):
		mp3blaster-3.2.0-0.pm0
		fuse-2.5.2-2.pm.0
		gpg-pubkey-1abd1afb-450ef738
		a52dec-0.7.4-3.pm.1
		glibc-html-2.5-25
		gnome-backgrounds-2.16.1-14
		gnome-icon-theme-2.16.0.1-12
		opensuse-quickstart_en-10.2-9
		susehelp_en-2006.06.20-25
		yast2-schema-2.14.2-3"""

		parts=line.strip().split('-')
		if len(parts)==2:
			return parts[0], try_cleanup_distro_version(parts[1])

		elif len(parts)<2:
			return None, None

		package='-'.join(parts[:-2])
		version=parts[-2]
		mod=parts[-1]

		return package, try_cleanup_distro_version(version+'-'+mod)
		
	def generate_cache(self):
		cache = []

		for line in os.popen("rpm -qa"):
			package, version = self.__parse_rpm_name(line)
			if package and version:
				cache.append('%s\t%s' % (package, version))

		cache.sort()   # Might be useful later; currently we don't care
		
		import tempfile
		fd, tmpname = tempfile.mkstemp(prefix = 'rpm-cache-tmp',
					       dir = self.cache_dir)
		try:
			stream = os.fdopen(fd, 'wb')
			stream.write('mtime: %d\n' % int(self.status_details.st_mtime))
			stream.write('size: %d\n' % self.status_details.st_size)
			stream.write('\n')
			for line in cache:
				stream.write(line + '\n')
			stream.close()

			os.rename(tmpname,
				  os.path.join(self.cache_dir,
					       self.cache_leaf))
		except:
			os.unlink(tmpname)
			raise

	def get_package_info(self, package, factory):
		try:
			version = self.versions[package]
		except KeyError:
			return

		impl = factory('package:rpm:%s:%s' % (package, version)) 
		impl.version = model.parse_version(version)

_host_distribution = None
def get_host_distribution():
	global _host_distribution
	if not _host_distribution:
		_dpkg_db_dir = '/var/lib/dpkg'
		_rpm_db_dir = '/var/lib/rpm'

		if os.access(_dpkg_db_dir, os.R_OK | os.X_OK):
			_host_distribution = DebianDistribution(_dpkg_db_dir)
		elif os.path.isdir(_rpm_db_dir):
			_host_distribution = RPMDistribution(_rpm_db_dir)
		else:
			_host_distribution = Distribution()
	
	return _host_distribution