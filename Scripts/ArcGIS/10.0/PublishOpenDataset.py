# ---------------------------------------------------------------------------
# PublishOpenDataset.py
# ---------------------------------------------------------------------------
# Publish a feature class from ArcSDE to the OpenColorado Data Catalog.
# The publishing process creats output files in a variety of formats that 
# can be shared via a web server. The script uses the CKAN client API to
# create the dataset on OpenColorado if it does not already exist. 
# If the dataset exists, its revision number will be incremented.
#----------------------------------------------------------------------------
# This script completes the following:
# 1) Exports the ArcSDE feature class to the download folder
#    in the following formats:
#
#    a. Shapefile (zipped)
#    b. CAD (dwg file)
#    c. KML (zipped KMZ)
#	 d. Metadata (xml)
#
# 	 The script automatically manages the creation of output folders if they
#    do not already exist.  Also creates temp folders for processing as
#    needed. The output folder has the following structure. You can start
#    with an empty folder and the script will create the necessary 
#	 directories.
#
#		<output_folder>
#		    |- <dataset_name> (catalog dataset name with prefix removed, 
#							   dashes replaced with underscores)
#			    |- shape
#				    |- <dataset_name>.zip
#		        |- cad
#				    |- <dataset_name>.dwg
#		        |- kml 
#				    |- <dataset_name>.kmz
#		        |- metadata 
#				    |- <dataset_name>.xml
#
# 2) Reads the exported ArcGIS Metadata xml file and parses the relevant
#	 metadata fields to be published to the OpenColorado Data Repository.
#
# 3) Uses the CKAN client API to create a new dataset on the OpenColorado
#	 Data Repository if the dataset does not already exist. If the dataset
#	 already exists, it is updated. 
#
# 4) Updates the version (revision) number of the dataset on the OpenColorado
#    Data Catalog (if it already exists)
# ---------------------------------------------------------------------------

# Import system modules
import sys, os, arcpy, shutil, zipfile, glob, ckanclient, datetime, argparse
import xml.etree.ElementTree as et

# Global variables
args = None
output_folder = None
source_feature_class = None
ckan_client = None
temp_workspace = None
available_formats = ['shp','dwg','kml','metadata']

def main():
	"""Main function
	
	Returns:
        None
	"""
	global args, output_folder, source_feature_class, temp_workspace
	
	parser = argparse.ArgumentParser(fromfile_prefix_chars='@')
			
	# Optional arguments	   
	parser.add_argument('-o', '--output-folder',
		action='store', 
		dest='output_folder',
		help='The root output folder in which to create the published files.  Sub-folders will automatically be created for each dataset (ex. \\\\myserver\\OpenDataCatalog).')

	parser.add_argument('-w', '--temp-workspace',
		action='store', 
		dest='temp_workspace',
		help='The root workspace folder in which to create temporary output files. A local workspace will increase performance. (ex. C:\\temp).')
	
	parser.add_argument('-d', '--download-url',
		action='store', 
		dest='download_url',
		help='The root path to the data download repository. (ex. http://data.denvergov.org/).')
	
	parser.add_argument('-s', '--source-workspace',
		action='store', 
		dest='source_workspace', 
		required=True,
		help='The source workspace to publish the feature class from (ex. Database Connections\\\\SDE Connection.sde).  Backslashes must be escaped as in the example.')	
	
	parser.add_argument('-f', '--formats',
		action='store',
		dest='formats', 
		default='shp,dwg,kml,metadata',
		help='Specific formats to publish (shp=Shapefile, dwg=CAD drawing file, kml=Keyhole Markup Language, metadata=Metadata).  If not specified all formats will be published.')
		
	parser.add_argument('-a', '--ckan-api',
		action='store', 
		dest='ckan_api', 
		default='http://colorado.ckan.net/api/2/',
		help='The root path to the CKAN repository (ex. http://colorado.ckan.net/api/2/)')
		
	parser.add_argument('-k', '--ckan-api-key',
		action='store', 
		dest='ckan_api_key',
		required=True,
		help='The CKAN API key (get from http://colorado.ckan.net/user/me when logged in)')
	
	parser.add_argument('-p', '--ckan-dataset-name-prefix',
		action='store', 
		dest='ckan_dataset_name_prefix',
		default='',
		help='A prefix used in conjunction with the dataset-name argument to create the complete dataset name on OpenColorado.')

	parser.add_argument('-t', '--ckan-dataset-title-prefix',
		action='store', 
		dest='ckan_dataset_title_prefix',
		default='',
		help='A prefix used in conjunction with the dataset-title argument to create the complete dataset title on OpenColorado.')
	
	parser.add_argument('-g', '--ckan-group-name',
		action='store', 
		dest='ckan_group_name',
		default='',
		help='The group name in the OpenColorado group register that the dataset will be added to.')

	parser.add_argument('-l', '--ckan-license',
		action='store', 
		dest='ckan_license',
		default='cc-by',
		help='The default data license type for the dataset.')
	
	parser.add_argument('-i', '--ckan-increment-version',
		action='store', 
		dest='increment',
		choices=['major','minor','revision','none'],
		default='revision',
		help='Update the version number on OpenColorado \n(default: %(default)s)')
	
	parser.add_argument('-m', '--update-from-metadata',
		action='store', 
		dest='update_from_metadata',
		choices=['description','tags','all'],
		help='Update dataset information using the source metadata')

	parser.add_argument('-x', '--metadata-xslt',
		action='store', 
		dest='metadata_xslt',
		default='..\StyleSheets\Format_FGDC.xslt',
		help='The XSLT stylesheet to pass the FGDC CSDGM metadata through before publishing.')
		
	parser.add_argument('-v', '--verbose',
		action='store_true', 
		dest='verbose', 
		help='Verbose output messages')
		
	# Positional arguments
	parser.add_argument('feature_class',
		action='store',
		help='The fully qualified path to the feature class (ex. Database Connections\\\\SDE Connection.sde\\\\schema.parcels).  If a source workspace is specified (ex. -s Database Connections\\\\SDE Connection.sde) just the feature class name needs to be provided here (ex. schema.parcels)')
		
	parser.add_argument('dataset_name',
		action='store',
		help='The name of the dataset on OpenColorado.  If a prefix is provided (-p) don''t include it here.')

	parser.add_argument('dataset_title',
		action='store',
		help='The title of the dataset on OpenColorado.  If a prefix is provided (-t) don''t include it here.')
			
	args = parser.parse_args()
	
	# Set the global output folder (trim and append a slash to make sure the files get created inside the directory)
	if args.output_folder != None:
		output_folder = args.output_folder.strip()

	# Set the global temp workspace folder (trim and append a slash to make sure the files get created inside the directory)
	if args.temp_workspace != None:
		temp_workspace = args.temp_workspace.strip()		
	
	# Set the source feature class
	if args.source_workspace == None:
		source_feature_class = args.feature_class
	else:
		source_feature_class = os.path.join(args.source_workspace,args.feature_class)
			
	# If no formats are specified then enable all formats
	if args.formats == None:
		args.formats = available_formats
	else:
		args.formats = args.formats.split(',')
		
		# Validate that the format types passed in are valid
		for arg in args.formats:
			if not arg in available_formats:
				raise Exception(str.format("Format type: '{0}' not supported", arg))

	info('Starting ' + sys.argv[0])
	debug(' Feature class: ' + source_feature_class)
	debug(' Dataset name: ' + args.dataset_name)
	debug(' Publish folder: ' + output_folder)
	
	try:
						
		# Create the dataset folder and update the output folder
		output_folder = create_dataset_folder()
		
		# Create temporary folder for processing and update the temp workspace folder
		temp_workspace = create_dataset_temp_folder()
	
		# Export to the various file formats
		if 'shp' in args.formats:
			info('Exporting to shapefile')
			export_shapefile()
		
		if 'dwg' in args.formats:
			info('Exporting to CAD drawing file')
			export_cad()
	
		if 'kml' in args.formats:
			info('Exporting to kml file')
			export_kml()
			
		if 'metadata' in args.formats:
			info('Exporting metadata XML file')
			export_metadata()
		
		# Update the dataset information on the CKAN repository
		publish_to_ckan()
		
		# Delete the dataset temp folder
		delete_dataset_temp_folder()
		
		info('Completed ' + sys.argv[0])
		
	except:
		if info:
			info("Error! {0} {1}".format(sys.exc_info()[1], sys.exc_info()[0]))
			info(arcpy.GetMessages(2))
			info("Exiting program.")
			
		sys.exit(1)
		
		
def publish_to_ckan():
	"""Updates the dataset in the CKAN repository or creates a new dataset

	Returns:
	    None
	"""
	global ckan_client
	
	# Initialize the CKAN client  
	ckan_client = ckanclient.CkanClient(base_location=args.ckan_api,api_key=args.ckan_api_key)
	
	# Create the name of the dataset on the CKAN instance
	dataset_id = args.ckan_dataset_name_prefix + args.dataset_name
	
	# Get the dataset from CKAN
	dataset_entity = get_remote_dataset(dataset_id)
	
	# Check to see if the dataset exists on CKAN or not
	if dataset_entity is None:

		# Create a new dataset
		create_dataset(dataset_id)
		
	else:
		
		# Update an existing dataset
		update_dataset(dataset_entity)

	# Update the dataset version on the CKAN repository (causes the last modified date to be updated)
	if args.increment != "none":
		info('Updating dataset version')
		update_dataset_version()
	
def create_folder(directory, delete=False):
	"""Creates a folder if it does not exist

	Returns:
	    None
	"""
	
	if os.path.exists(directory) and delete:
		debug('Deleting directory "' + directory)
		shutil.rmtree(directory)
		
	if not os.path.exists(directory):
		debug('Directory "' + directory + '" does not exist.  Creating..')
		os.makedirs(directory)
		
	return directory
		
def create_dataset_folder():
	"""Creates the output folder for exported files.
	
	Creates the output folder if it does not exist.

	Returns:
		The name of the path
	"""
	directory = os.path.join(output_folder,get_dataset_filename())
	create_folder(directory)
	
	return directory

def create_dataset_temp_folder():
	"""Creates a temporary folder for processing data
	
	Creates the temporary folder if it does not exist.

	Returns:
        The name of the path
	"""
	global temp_workspace
	
	directory = os.path.join(temp_workspace,get_dataset_filename())
	create_folder(directory)
		
	return directory

def delete_dataset_temp_folder():
	"""Deletes the temporary folder for processing data

	Returns:
        None
	"""
	global temp_workspace
	
	directory = os.path.join(temp_workspace)
	if os.path.exists(directory):
		debug('Deleting directory "' + directory)
		shutil.rmtree(directory)

def publish_file(directory, file_name, file_type):
	"""Publishes a file to the catalog download folder
	
	Returns:
        None
	"""

	folder = create_folder(os.path.join(output_folder,file_type))
	
	info(' Copying ' + file_name + ' to ' + folder)
	shutil.copyfile(os.path.join(directory,file_name), os.path.join(folder,file_name))

def get_dataset_filename():
	"""Gets a file system friendly name from the catalog dataset name
	
	Returns:
        A string representing the dataset file name
	"""
	global args
	return args.dataset_name.replace("-","_")

def get_dataset_title():
	"""Gets the title of the catalog dataset
	
	Returns:
        A string representing the dataset title
	"""
	global args

	# Create the dataset title
	return args.ckan_dataset_title_prefix + ": " + args.dataset_title
	
def export_shapefile():
	"""Exports the feature class as a zipped shapefile
	
	Returns:
        None
	"""
	folder = 'shape'
	name = get_dataset_filename()
	
	# Create a shape folder in the temp directory if it does not exist
	temp_working_folder = os.path.join(temp_workspace,folder)
	create_folder(temp_working_folder, True)
	
	# Create a folder for the shapefile (put in in a folder to zip)
	zip_folder = os.path.join(temp_working_folder,name)
	create_folder(zip_folder)

	# Export the shapefile to the folder
	source = source_feature_class
	destination = os.path.join(zip_folder,name + ".shp")
	
	# Export the shapefile
	debug(' - Exporting to shapefile from "' + source + '" to "' + destination + '"')
	arcpy.CopyFeatures_management(source, destination, "", "0", "0", "0")
	
	# Delete the metadata file generate from the shapefile export
	metadata_file_path = destination + ".xml"
	if os.path.exists(metadata_file_path):
		info("Deleting metadata for shapefile: " + metadata_file_path)
		os.remove(metadata_file_path)
	
	# Zip up the files
	debug(' - Zipping the shapefile')
	zip_file = zipfile.ZipFile(os.path.join(temp_working_folder,name + ".zip"), "w")
	
	for filename in glob.glob(zip_folder + "/*"):
		zip_file.write(filename, os.path.basename(filename), zipfile.ZIP_DEFLATED)
		
	zip_file.close()
	
	# Publish the zipfile to the download folder
	publish_file(temp_working_folder, name + ".zip","shape")
	
def export_cad():
	"""Exports the feature class as a CAD drawing file
	
	Returns:
        None
	"""
	folder = 'cad'
	name = get_dataset_filename()
	
	# Create a cad folder in the temp directory if it does not exist
	temp_working_folder = os.path.join(temp_workspace,folder)
	create_folder(temp_working_folder, True)
	
	# Export the shapefile to the folder
	source = source_feature_class
	destination = os.path.join(temp_working_folder,name + ".dwg")
	
	# Export the drawing file
	debug(' - Exporting to DWG file from "' + source + '" to "' + destination + '"')
	arcpy.ExportCAD_conversion(source, "DWG_R2000", destination, "Ignore_Filenames_in_Tables", "Overwrite_Existing_Files", "")
	
	# Publish the zipfile to the download folder
	publish_file(temp_working_folder, name + ".dwg","cad")
		
def export_kml():
	"""Exports the feature class to a kml file
	
	Returns:
        None
	"""
	arcpy.CheckOutExtension("3D")
	
	folder = 'kml'
	name = get_dataset_filename()
	
	# Create a kml folder in the temp directory if it does not exist
	temp_working_folder = os.path.join(temp_workspace,folder)
	create_folder(temp_working_folder, True)
	
	# Export the feature class to a temporary file gdb
	source = source_feature_class
	destination = os.path.join(temp_working_folder,name + ".kmz")
	
	gdb_temp = os.path.join(temp_working_folder,name + ".gdb")
	gdb_feature_class = os.path.join(gdb_temp,name)
	
	debug(' - Creating temporary file geodatabase for processing:' + gdb_temp)
	if not arcpy.Exists(gdb_temp):
		arcpy.CreateFileGDB_management(os.path.dirname(gdb_temp),os.path.basename(gdb_temp)) 
		
	debug(' - Copying feature class to:' + gdb_feature_class)
	arcpy.CopyFeatures_management(source, gdb_feature_class)

	# Make a feature layer (in memory)
	debug(' - Generating KML file in memory from  "' + gdb_feature_class + '"')
	arcpy.MakeFeatureLayer_management(gdb_feature_class, name, "", "")
	
	# Encode special characters that don't convert to KML correctly.
	# Replace any literal nulls <Null> with empty as these don't convert to KML correctly
	replace_literal_nulls(name)

	# Convert the layer to KML
	debug(' - Exporting KML file (KMZ) to "' + destination + '"')
	arcpy.LayerToKML_conversion(name, destination, "20000", "false", "DEFAULT", "1024", "96")
		
	# Delete the in-memory feature layer and the file geodatabase
	debug(' - Deleting in-memory feature layer:' + name)
	arcpy.Delete_management(name)
	
	debug(' - Deleting temporary file geodatabase:' + gdb_temp)
	arcpy.Delete_management(gdb_temp)
	
	# Publish the zipfile to the download folder
	publish_file(temp_working_folder, name + ".kmz","kml")
	
def export_metadata():
	"""Exports the feature class metadata to an xml file
	
	Returns:
        None
	"""	
	
	folder = 'metadata'
	name = get_dataset_filename()
	
	# Create a kml folder in the temp directory if it does not exist
	temp_working_folder = os.path.join(temp_workspace,folder)
	create_folder(temp_working_folder, True)
	
	# Set the destinion of the metadata export
	source = source_feature_class
	raw_metadata_export = os.path.join(temp_working_folder,name + "_raw.xml")
	
	# Export the metadata
	arcpy.env.workspace = temp_working_folder
	installDir = arcpy.GetInstallInfo("desktop")["InstallDir"]
	translator = installDir + "Metadata/Translator/ARCGIS2FGDC.xml"
	arcpy.ExportMetadata_conversion(source, translator, raw_metadata_export)

	# Process: XSLT Transformation to remove any sensitive info or format
	destination = os.path.join(temp_working_folder,name + ".xml")	
	if os.path.exists(args.metadata_xslt):
		arcpy.XSLTransform_conversion(raw_metadata_export, args.metadata_xslt, destination, "")
	else:
		# If no transformation exists, just rename and publish the raw metadata
		os.rename(raw_metadata_export, destination)
		
	# Publish the metadata to the download folder
	publish_file(temp_working_folder, name + ".xml","metadata")

def replace_literal_nulls(layer_name):
	"""Replaces literal string representation of null, '<Null>', with a true null value 
		(None in Python).
	
	Parameters:
		layer_name - The name of the layer to replace literal nulls.
		
	Returns:
		None
	"""
	debug(' - Start replacing literal nulls.')
	
	fields, row, rows = None, None, None
	
	try:
			
		# Create a list of field objects.
		fields = arcpy.ListFields(layer_name)
		
		# Create an update cursor that will loop through and update each row.
		rows = arcpy.UpdateCursor(layer_name)
		
		# Loop through each row and field and replace literal nulls.
		for row in rows:
			
			for field in fields:
				if field.type == 'String':
					value = str(row.getValue(field.name))
					
					if (value.find('<Null>') > -1): 

						debug(' - Found a "<Null>" string to nullify in field: {0}.'.format(field.name))
						debug(' - Replacing null string')
						row.setValue(field.name, None)
						debug(' - Replaced with {0}'.format(value))
						
						# Update row
						rows.updateRow(row)
		
		debug('Done replacing literal nulls in {0}.'.format(layer_name))
			
	finally: # Clean up
		if row:
			del row
		if rows:
			del rows
		
		
def get_remote_dataset(dataset_id):
	"""Gets the dataset from CKAN repository

	Parameters:
		dataset_id - A string representing the unique dataset name
	
	Returns:
        An object structured the same as the JSON dataset output from
        the CKAN REST API. For more information on the structure look at the
        web service JSON output, or reference:
        http://docs.ckan.org/en/latest/api-v2.html#model-api
	"""
	dataset_entity = None

	try:
		# Get the dataset
		dataset_entity = ckan_client.package_entity_get(dataset_id)
		info(" Dataset " + dataset_id + " found on OpenColorado")
		
	except ckanclient.CkanApiNotFoundError:
		info(" Dataset " + dataset_id + " not found on OpenColorado")

	return dataset_entity

def create_dataset(dataset_id):
	"""Creates a new dataset and registers it to CKAN

	Parameters:
		dataset_id - A string representing the unique dataset name
	
	Returns:
		None
	"""	
	
	# Create a new dataset locally		
	dataset_entity = create_local_dataset(dataset_id)
	
	# Update the dataset's resources (download links)
	dataset_entity = update_dataset_resources(dataset_entity)
	
	# Update the dataset from ArcGIS Metadata if configured
	if (args.update_from_metadata != None and 'metadata' in args.formats):
		dataset_entity = update_local_dataset_from_metadata(dataset_entity)
	
	# Create a new dataset in CKAN
	create_remote_dataset(dataset_entity)
		
def create_local_dataset(dataset_id):
	"""Creates a new dataset entity, but does not commit it to CKAN

	Parameters:
		dataset_id - A string representing the unique dataset name
	
	Returns:
        An object structured the same as the JSON dataset output from
        the CKAN REST API. For more information on the structure look at the
        web service JSON output, or reference:
        http://docs.ckan.org/en/latest/api-v2.html#model-api
	"""	

	global args, ckan_client	

	info(' New Dataset ' + dataset_id + ' being initialized')
	dataset_entity = {};
	dataset_entity['name'] = dataset_id
	dataset_entity['license_id'] = args.ckan_license
	dataset_entity['title'] = get_dataset_title()

	# Find the correct CKAN group id to assign the dataset to
	try:
		group_entity = ckan_client.group_entity_get(args.ckan_group_name)
		if group_entity is not None:
			info(' Adding dataset to group: ' + args.ckan_group_name)		
			dataset_entity['groups'] = [group_entity['id']]
	except ckanclient.CkanApiNotFoundError:
		info(' Group: ' + args.ckan_group_name + ' not found on OpenColorado')
		dataset_entity['groups'] = []		

	return dataset_entity

def create_remote_dataset(dataset_entity):
	"""Creates a new remote CKAN dataset.
	   The dataset does not yet exists in the CKAN repository, it is created.
	
	Parameters:
        dataset_entity - An object structured the same as the JSON dataset 
        output from the CKAN REST API. For more information on the structure 
        look at the web service JSON output, or reference:
        http://docs.ckan.org/en/latest/api-v2.html#model-api
    
    Returns:
    	None
	"""	
	global ckan_client	

	# Create a new dataset on OpenColorado 
	ckan_client.package_register_post(dataset_entity)

def update_dataset(dataset_entity):
	"""Updates an existing dataset and commits changes to CKAN

	Parameters:
        dataset_entity - An object structured the same as the JSON dataset 
        output from the CKAN REST API. For more information on the structure 
        look at the web service JSON output, or reference:
        http://docs.ckan.org/en/latest/api-v2.html#model-api
	
	Returns:
		None
	"""	
	
	# Update the dataset's resources (download links)
	dataset_entity = update_dataset_resources(dataset_entity)
	
	# Update the dataset's licensing
	dataset_entity['license_id'] = args.ckan_license
	
	# Update the dataset's title
	dataset_entity['title'] = get_dataset_title()

	# Update the dataset from ArcGIS Metadata if configured
	if (args.update_from_metadata != None and 'metadata' in args.formats):
		dataset_entity = update_local_dataset_from_metadata(dataset_entity)

	# Update existing dataset in CKAN		
	update_remote_dataset(dataset_entity)

def update_dataset_resources(dataset_entity):
	"""Updates the CKAN dataset entity resources. If the resources already
	   exist in the CKAN repository, they updated (preserving the original 
	   unique resource ID). If the resource does not already exist,
	   a new one is created. 
	   
	Parameters:
		dataset_entity - An object structured the same as the JSON dataset output from
		the CKAN REST API. For more information on the structure look at the
		web service JSON output, or reference:
		http://docs.ckan.org/en/latest/api-v2.html#model-api
	
	Returns:
        An object structured the same as the JSON dataset output from
        the CKAN REST API. For more information on the structure look at the
        web service JSON output, or reference:
        http://docs.ckan.org/en/latest/api-v2.html#model-api
	"""		
	global args, ckan_client
	
	# Initialize an empty array of resources
	resources = []
	
	# If the dataset has existing resources, update them
	if ('resources' in dataset_entity):
		resources = dataset_entity['resources']
	
	# Construct the file resource download urls
	dataset_file_name = get_dataset_filename() 
	
	# Get the dataset title
	title = get_dataset_title()	
	
	# Export to the various file formats
	if 'shp' in args.formats:
		
		shp_resource = get_resource_by_format(resources, 'shp')
		
		if (shp_resource is None):
			info('Creating new SHP resource')
			shp_resource = {}
			resources.append(shp_resource)
		else:			
			info('Updating SHP resource')
		
		shp_resource['name'] = args.dataset_title + ' - SHP'
		shp_resource['description'] = title + ' - SHP'
		shp_resource['url'] = args.download_url + dataset_file_name + '/shape/' + dataset_file_name + '.zip'
		shp_resource['mimetype'] = 'application/zip'
		shp_resource['format'] = 'SHP'
	
	if 'dwg' in args.formats:

		dwg_resource = get_resource_by_format(resources, 'dwg')
		
		if (dwg_resource is None):
			info('Creating new DWG resource')
			dwg_resource = {}
			resources.append(dwg_resource)		
		else:			
			info('Updating DWG resource')
		
		dwg_resource['name'] = args.dataset_title + ' - DWG'
		dwg_resource['description'] = title + ' - DWG'
		dwg_resource['url'] = args.download_url + dataset_file_name + '/cad/' + dataset_file_name + '.dwg'
		dwg_resource['mimetype'] = 'application/acad'
		dwg_resource['format'] = 'DWG'	

	if 'kml' in args.formats:
		
		kml_resource = get_resource_by_format(resources, 'kml')
		
		if (kml_resource is None):
			info('Creating new KML resource')		
			kml_resource = {}
			resources.append(kml_resource)
		else:			
			info('Updating KML resource')

		kml_resource['name'] = args.dataset_title + ' - KML'
		kml_resource['description'] = title + ' - KML'
		kml_resource['url'] = args.download_url + dataset_file_name + '/kml/' + dataset_file_name + '.kmz'
		kml_resource['mimetype'] = 'application/vnd.google-earth.kmz'
		kml_resource['format'] = 'KML'

	if 'metadata' in args.formats:
		
		metadata_resource = get_resource_by_format(resources, 'XML')
		
		if (metadata_resource is None):
			info('Creating new Metadata resource')		
			metadata_resource = {}
			resources.append(metadata_resource)
		else:			
			info('Updating Metadata resource')

		metadata_resource['name'] = args.dataset_title + ' - Metadata'
		metadata_resource['description'] = title + ' - Metadata'
		metadata_resource['url'] = args.download_url + dataset_file_name + '/metadata/' + dataset_file_name + '.xml'
		metadata_resource['mimetype'] = 'application/xml'
		metadata_resource['format'] = 'XML'
					
	# Update the resources on the dataset					
	dataset_entity['resources'] = resources;
	
	return dataset_entity

def get_resource_by_format(resources, format_type):
	"""Searches an array of resources to find the resource that
	   matches the file format type passed in. Returns the resource
	   if found. 
	   
	Parameters:
		resources - An array of CKAN dataset resources 
		For more information on the structure look at the
		web service JSON output, or reference:
		http://docs.ckan.org/en/latest/api-v2.html#model-api
		format_type - A string with the file format type to find (SHP, KML..)
	
	Returns:
		resource - A CKAN dataset resource if found. None if not found. 
		For more information on the structure look at the
		web service JSON output, or reference:
		http://docs.ckan.org/en/latest/api-v2.html#model-api
	"""		
	
	for resource in resources:
		current_format = resource['format']
		if (str(current_format).strip().upper() == format_type.strip().upper()):
			return resource
	
	return None 
			
def update_local_dataset_from_metadata(dataset_entity):
	"""Updates the CKAN dataset entity by reading in metadata
	   from the ArcGIS Metadata xml file. If the dataset already
	   exists in the CKAN repository, the dataset is fecthed
	   and modified. If the dataset does not already exist,
	   a new dataset entity object is created. 
	   
	Parameters:
		dataset_entity - An object structured the same as the JSON dataset output from
		the CKAN REST API. For more information on the structure look at the
		web service JSON output, or reference:
		http://docs.ckan.org/en/latest/api-v2.html#model-api
	
	Returns:
        An object structured the same as the JSON dataset output from
        the CKAN REST API. For more information on the structure look at the
        web service JSON output, or reference:
        http://docs.ckan.org/en/latest/api-v2.html#model-api
	"""
		
	# Reconstruct the name of the file
	folder = 'metadata'
	name = get_dataset_filename()
	temp_working_folder = os.path.join(temp_workspace,folder)
	file_path = os.path.join(temp_working_folder,name + ".xml")
	
	# Open the file and read in the xml
	metadata_file = open(file_path,"r")
	metadata_xml = et.parse(metadata_file)
	
	# Update the dataset title
	title = get_dataset_title()
	dataset_entity['title'] = title	

	# Get the abstract
	xpath_abstract = '//abstract'
	abstract_element = metadata_xml.find(xpath_abstract)
	if (abstract_element is not None):
		dataset_entity['notes'] = abstract_element.text
	else:
		debug('No abstract found in metadata');

	# Get the maintainer
	xpath_maintainer= '//distinfo/distrib/cntinfo/cntorgp/cntorg'
	maintainer_element = metadata_xml.find(xpath_maintainer)
	if (maintainer_element != None):
		dataset_entity['maintainer'] = maintainer_element.text
	else:
		debug('No maintainer found in metadata');

	# Get the maintainer email
	xpath_maintainer_email = '//distinfo/distrib/cntinfo/cntemail'
	maintainer_email_element = metadata_xml.find(xpath_maintainer_email)
	if (maintainer_email_element != None):
		dataset_entity['maintainer_email'] = maintainer_email_element.text
	else:
		debug('No maintainer email found in metadata');

	# Get the author
	xpath_author = '//idinfo/citation/citeinfo/origin'
	author_element = metadata_xml.find(xpath_author)
	if (author_element != None):
		dataset_entity['author'] = author_element.text
	else:
		debug('No author found in metadata');

	# Get the author_email
	dataset_entity['author_email'] = ''

	# Get the keywords
	keywords = []
	
	# If the dataset has existing tags, check for the 'featured'
	# tag and preserve it if it exists
	if ('tags' in dataset_entity):
		if ('featured' in dataset_entity['tags']):
			keywords.append('featured')
			debug ('Preserving featured dataset tag');

	# Get the theme keywords from the ArcGIS Metadata
	xpath_theme_keys = '//themekey'
	theme_keyword_elements = metadata_xml.findall(xpath_theme_keys)
	
	# Get the place keywords from the ArcGIS Metadata
	xpath_place_keys = '//placekey'
	place_keyword_elements = metadata_xml.findall(xpath_place_keys)	
	
	# Combine the lists
	keyword_elements = theme_keyword_elements + place_keyword_elements
	
	for keyword_element in keyword_elements:
		keyword = keyword_element.text
		keyword = keyword.lower().replace(' ','-')
		keywords.append(keyword)
		debug ('Keywords found in metadata: ' + keyword);

	dataset_entity['tags'] = keywords

	return dataset_entity

def update_remote_dataset(dataset_entity):
	"""Updates the remote CKAN dataset.
	   The dataset already exists in the CKAN repository, it is updated.
	
	Parameters:
        dataset_entity - An object structured the same as the JSON dataset 
        output from the CKAN REST API. For more information on the structure 
        look at the web service JSON output, or reference:
        http://docs.ckan.org/en/latest/api-v2.html#model-api
    
    Returns:
    	None
	"""	
	global ckan_client
	
	ckan_client.package_entity_put(dataset_entity)

def update_dataset_version():
	"""Updates the dataset version number on CKAN repository
	
	Returns:
        None
	"""	
	global args
	
	# Initialize CKAN client
	ckan = ckanclient.CkanClient(base_location=args.ckan_api,api_key=args.ckan_api_key)
	
	# Create the name of the dataset on the CKAN instance
	dataset_id = args.ckan_dataset_name_prefix + args.dataset_name
	
	try:
		# Get the dataset
		dataset_entity = ckan.package_entity_get(dataset_id)
		
		# Increment the version number
		version = dataset_entity['version']
		version = increment_version(version, args.increment)
		dataset_entity['version'] = version
		
		# Update the dataset
		ckan.package_entity_put(dataset_entity)
		
	except ckanclient.CkanApiNotFoundError:
		info(" Dataset " + dataset_id + " not found on OpenColorado")

def increment_version(version, increment_type):
	"""Increments the version number
	
	Parameters:
		version - A version in the format <major.minor.revision>
		increment_type - [major, minor, or revision]
	
	Returns:
        a string representing the incremented version in the format
        <major.minor.revision>
	"""
	incremented_version = version
	
	if version == None:
		incremented_version = "1.0.0"
		info ('No version number found.  Setting version to ' + incremented_version);
	else:
		version_parts = version.split(".")
		if len(version_parts) == 3:
			major = int(version_parts[0])
			minor = int(version_parts[1])
			revision = int(version_parts[2])
			
			if increment_type == 'major':
				major = major + 1
			elif increment_type == 'minor':
				minor = minor + 1
			elif increment_type == 'revision':	  
				revision = revision + 1
				
			incremented_version = str(major) + "." + str(minor) + "." + str(revision)
			info ('Incrementing CKAN dataset version from ' + version + ' to ' + incremented_version);
	return incremented_version

def debug(message) :
	global args
	if args.verbose:
		log(message)

def info(message) :
	log(message)
	
def log(message):
	now = datetime.datetime.now()
	print now.strftime("%Y-%m-%d %H:%M") + ": " + message
	
	
#Execute main function	
if __name__ == '__main__':
	main()
	
