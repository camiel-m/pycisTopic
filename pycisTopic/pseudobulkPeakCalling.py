import pyranges as pr
import pyBigWig
import pandas as pd
import ray
import logging
import sys
import subprocess
from typing import Optional, Union
from typing import List, Dict
from .cisTopicClass import *
import os

def exportPseudoBulk(input_data: Union['cisTopicObject', pd.DataFrame, Dict[str, pd.DataFrame]],
					 variable: str,
					 chromsizes: Union[pd.DataFrame, pr.PyRanges],
					 bed_path: str,
					 bigwig_path: str,
					 path_to_fragments: Optional[Dict[str, str]] = None,
					 n_cpu: Optional[int] = 1,
					 normalize_bigwig: Optional[bool] = True,
					 remove_duplicates: Optional[bool] = True):
	"""
	Create pseudobulks as bed and bigwig from single cell fragments file given a barcode annotation. 

	Parameters
	---------
	input_data: cisTopicObject or pd.DataFrame
		A :class:`cisTopicObject` containing the specified `variable` as a column in :class:`cisTopicObject.cell_data` or a cell metadata 
		:class:`pd.DataFrame` containing barcode as rows, containing the specified `variable` as a column (additional columns are
		possible) and a sample_id column. Index names must be in the format BARCODE-sample_id (e.g. ATGGTCCTGT-Sample_1)
	variable: str
		A character string indicating the column that will be used to create the different group pseudobulk. It must be included in 
		the cell metadata provided as input_data.
	chromsizes: pd.DataFrame or pr.PyRanges
		A data frame or :class:`pr.PyRanges` containing size of each column, containing 'Chromosome', 'Start' and 'End' columns.
	bed_path: str
		Path to folder where the fragments bed files per group will be saved.
	bigwig_path: str
		Path to folder where the bigwig files per group will be saved.
	path_to_fragments: str or dict
		A dictionary of character strings, with sample name as names indicating the path to the fragments file/s from which pseudobulk profiles have to
		be created. If a :class:`cisTopicObject` is provided as input it will be ignored, but if a cell metadata :class:`pd.DataFrame` is provided it
		is necessary to provide it. The keys of the dictionary need to match with the sample_id tag added to the index names of the input data frame. 
	n_cpu: int
		Number of cores to use. Default: 1.	
	normalize_bigwig: bool
		Whether bigwig files should be CPM normalized. Default: True.
	remove_duplicates: bool
		Whether duplicates should be removed before converting the data to bigwig.
		
	Return
	------
	dict
		A dictionary containing the paths to the newly created bed fragments files per group a dictionary containing the paths to the
		newly created bigwig files per group.
	"""
	# Create logger
	level	= logging.INFO
	format   = '%(asctime)s %(name)-12s %(levelname)-8s %(message)s'
	handlers = [logging.StreamHandler(stream=sys.stdout)]
	logging.basicConfig(level = level, format = format, handlers = handlers)
	log = logging.getLogger('cisTopic')
	
	# Get fragments file
	if isinstance(input_data, cisTopicObject):
		path_to_fragments = cisTopic_obj.path_to_fragments
		if path_to_fragments == None:
			log.error('No fragments path in this cisTopic object. A fragments file is needed for forming pseudobulk profiles.')
		if len(path_to_fragments) > 1:
			path_to_project = [cell_data.loc[cell_data['path_to_fragments'] == path_to_fragments[x], 'cisTopic_id'][0] for x in range(len(path_to_fragments))]
			path_to_fragments = {path_to_project[x]: path_to_fragments[x] for x in range(len(path_to_fragments))}
		cell_data = cisTopic_obj.cell_data
	elif isinstance(input_data, pd.DataFrame):
		if path_to_fragments == None:
			log.error('Please, provide path_to_fragments.')
		cell_data = input_data
		cell_suffixes = list(set([input_data.index.tolist()[x].split('-')[-1] for x in range(len(input_data.index.tolist()))]))
	# Get fragments
	if isinstance(path_to_fragments , dict):
		fragments_df_dict={}
		for sample_id in path_to_fragments.keys():
			if isinstance(input_data, pd.DataFrame):
				if sample_id not in cell_suffixes:
					log.error('Check that your cell suffixes match with the keys in your path_to_fragments dictionary')	
		log.info('Reading fragments from ' + path_to_fragments[sample_id])
		fragments_df=pr.read_bed(path_to_fragments[sample_id], as_df=True)
		if fragments_df.loc[:,'Name'][0].find('-')!=-1:
			log.info('Barcode correction')
			fragments_df.loc[:,'Name'] = [x.rstrip("-1234567890") for x in fragments_df.loc[:,'Name']]
			log.info('Barcode correction completed')
		fragments_df.loc[:,'Name'] = fragments_df.loc[:,'Name'] + '-' + sample_id
		fragments_df = fragments_df.loc[fragments_df['Name'].isin(cell_data.index.tolist())]	
		if len(path_to_fragments) > 1:
			fragments_df_dict[sample_id] = fragments_df
			fragments_df_list = [fragments_df_dict[list(fragments_df_dict.keys())[x]] for x in range(len(path_to_fragments))]
			log.info('Merging fragments')
			fragments_df = fragments_df_list[0].append(fragments_df_list[1:])
	else:
		if isinstance(input_data, cisTopicObject):
			log.info('Reading fragments')
			fragments_df = pr.read_bed(path_to_fragments, as_df=True)
			fragments_df = fragments_df.loc[fragments_df['Name'].isin(cell_data.index.tolist())]
	# Set groups
	group_var = cell_data.loc[:,variable]
	groups = sorted(list(set(group_var)))
	# Check chromosome sizes
	if isinstance(chromsizes, pd.DataFrame):
		chromsizes = chromsizes.loc[:,['Chromosome', 'Start', 'End']]
		chromsizes = pr.PyRanges(chromsizes)
	# Check that output paths exist
	if not os.path.exists(bed_path):
		os.makedirs(bed_path)
	if not os.path.exists(bigwig_path):
		os.makedirs(bigwig_path)
	# Create pseudobulks
	ray.init(num_cpus = n_cpu)
	paths = ray.get([exportPseudoBulk_ray.remote(group_var,
								group,
								fragments_df, 
								chromsizes,
								bigwig_path,
								bed_path,
								normalize_bigwig,
								remove_duplicates) for group in groups])
	ray.shutdown()
	bw_paths = {list(paths[x].keys())[0]:paths[x][list(paths[x].keys())[0]][0] for x in range(len(paths))}
	bed_paths = {list(paths[x].keys())[0]:paths[x][list(paths[x].keys())[0]][1] for x in range(len(paths))}
	return bw_paths, bed_paths

@ray.remote
def exportPseudoBulk_ray(group_var: pd.DataFrame,
						 group: str,
						 fragments_df: pd.DataFrame,
						 chromsizes: pr.PyRanges,
						 bigwig_path: str,
						 bed_path: str,
						 normalize_bigwig: Optional[bool] = True,
					 	 remove_duplicates: Optional[bool] = True):
	"""
	Create pseudobulk as bed and bigwig from single cell fragments file given a barcode annotation and a group. 

	Parameters
	---------
	group_var: pd.Series
		A cell metadata :class:`pd.Series` containing barcodes and their annotation.
	group: str
		A character string indicating the group for which pseudobulks will be created.
	fragments_df: pd.DataFrame
		A data frame containing 'Chromosome', 'Start', 'End', 'Name', and 'Score', which indicates the number of times that a 
		fragments is found assigned to that barcode. 
	chromsizes: pr.PyRanges
		A :class:`pr.PyRanges` containing size of each column, containing 'Chromosome', 'Start' and 'End' columns.
	bed_path: str
		Path to folder where the fragments bed file will be saved.
	bigwig_path: str
		Path to folder where the bigwig file will be saved.
	normalize_bigwig: bool
		Whether bigwig files should be CPM normalized. Default: True.
	remove_duplicates: bool
		Whether duplicates should be removed before converting the data to bigwig.
		
	Return
	------
	dict
		A dictionary containing the path to the newly created bed and bigwig files.
	"""
	# Create logger
	level	= logging.INFO
	format   = '%(asctime)s %(name)-12s %(levelname)-8s %(message)s'
	handlers = [logging.StreamHandler(stream=sys.stdout)]
	logging.basicConfig(level = level, format = format, handlers = handlers)
	log = logging.getLogger('cisTopic')
	
	log.info('Creating pseudobulk for '+ str(group))
	barcodes=group_var[group_var.isin([group])].index.tolist()
	group_fragments=fragments_df.loc[fragments_df['Name'].isin(barcodes)]
	group_pr=pr.PyRanges(group_fragments)
	bigwig_path_group = bigwig_path + str(group) + '.bw'
	bed_path_group = bed_path + str(group) + '.bed.gz'
	if isinstance(bigwig_path, str):
		if remove_duplicates == True:
			group_pr.to_bigwig(path=bigwig_path_group, chromosome_sizes=chromsizes, rpm=normalize_bigwig)
		else:
			group_pr.to_bigwig(group_pr, path=bigwig_path_group, chromsizes=chromsizes, rpm=normalize_bigwig, value_col='Score')
	if isinstance(bed_path, str):
		group_pr.to_bed(path=bed_path_group, keep=True, compression='infer', chain=False)
	log.info(str(group)+' done!')
	return {group: [bigwig_path_group, bed_path_group]}

def peakCalling(macs_path: str,
				bed_paths: Dict,
			 	outdir: str,
			 	genome_size: str,
			 	n_cpu: Optional[int] = 1,
			 	input_format: Optional[str] = 'BEDPE',
			 	shift: Optional[int] = 73,
			 	ext_size: Optional[int] = 146,
			 	keep_dup: Optional[str] = 'all',
			 	q_value: Optional[float] = 0.05):
	"""
	Performs pseudobulk peak calling with MACS2. It requires to have MACS2 installed (https://github.com/macs3-project/MACS).

	Parameters
	---------
	macs_path: str
		Path to MACS binary (e.g. /xxx/MACS/xxx/bin/macs2).
	bed_paths: dict
		A dictionary containing group label as name and the path to their corresponding fragments bed file as value.
	outdir: str
		Path to the output directory. 
	genome_size: str
		Effective genome size which is defined as the genome size which can be sequenced. Possible values: 'hs', 'mm', 'ce' and 'dm'.
	n_cpu: int
		Number of cores to use. Default: 1.	
	input_format: str
		Format of tag file can be ELAND, BED, ELANDMULTI, ELANDEXPORT, SAM, BAM, BOWTIE, BAMPE, or BEDPE. Default is AUTO which will
		allow MACS to decide the format automatically. Default: 'BEDPE'.
	shift: int
		To set an arbitrary shift in bp. For finding enriched cutting sites (such as in ATAC-seq) a shift of 73 bp is recommended.
		Default: 73.
	ext_size: int
		To extend reads in 5'->3' direction to fix-sized fragment. For ATAC-seq data, a extension of 146 bp is recommended. 
		Default: 146.
	keep_dup: str
		Whether to keep duplicate tags at te exact same location. Default: 'all'.
	q_value: float
		The q-value (minimum FDR) cutoff to call significant regions. Default: 0.05.
	
	Return
	------
	dict
		A dictionary containing each group label as names and :class:`pr.PyRanges` with MACS2 narrow peaks as values.
	"""
	ray.init(num_cpus=n_cpu)
	narrow_peaks = ray.get([MACS_callPeak_ray.remote(macs_path,
								bed_paths[name],
								name,
								outdir, 
								genome_size,
								input_format,
								shift,
								ext_size,
								keep_dup,
								q_value) for name in list(bed_paths.keys())])
	ray.shutdown()
	narrow_peaks_dict={list(bed_paths.keys())[i]: narrow_peaks[i].narrow_peak for i in range(len(narrow_peaks))} 
	return narrow_peaks_dict


@ray.remote
def MACS_callPeak_ray(macs_path: str,
					  bed_path: str,
					  name: str,
					  outdir: str,
					  genome_size: str,
					  input_format: Optional[str] = 'BEDPE',
				 	  shift: Optional[int] = 73,
				 	  ext_size: Optional[int] = 146, 
				  	  keep_dup: Optional[str] = 'all',
				 	  q_value: Optional[int] = 0.05):
	"""
	Performs pseudobulk peak calling with MACS2 in a group. It requires to have MACS2 installed (https://github.com/macs3-project/MACS).

	Parameters
	---------
	macs_path: str
		Path to MACS binary (e.g. /xxx/MACS/xxx/bin/macs2).
	bed_path: str
		Path to fragments file bed file.
	name: str
		Name of string of the group.
	outdir: str
		Path to the output directory. 
	genome_size: str
		Effective genome size which is defined as the genome size which can be sequenced. Possible values: 'hs', 'mm', 'ce' and 'dm'.
	input_format: str
		Format of tag file can be ELAND, BED, ELANDMULTI, ELANDEXPORT, SAM, BAM, BOWTIE, BAMPE, or BEDPE. Default is AUTO which will
		allow MACS to decide the format automatically. Default: 'BEDPE'.
	shift: int
		To set an arbitrary shift in bp. For finding enriched cutting sites (such as in ATAC-seq) a shift of 73 bp is recommended.
		Default: 73.
	ext_size: int
		To extend reads in 5'->3' direction to fix-sized fragment. For ATAC-seq data, a extension of 146 bp is recommended. 
		Default: 146.
	keep_dup: str
		Whether to keep duplicate tags at te exact same location. Default: 'all'.
	q_value: float
		The q-value (minimum FDR) cutoff to call significant regions. Default: 0.05.
	
	Return
	------
	dict
		A :class:`pr.PyRanges` with MACS2 narrow peaks as values.
	"""
	# Create logger
	level	= logging.INFO
	format   = '%(asctime)s %(name)-12s %(levelname)-8s %(message)s'
	handlers = [logging.StreamHandler(stream=sys.stdout)]
	logging.basicConfig(level = level, format = format, handlers = handlers)
	log = logging.getLogger('cisTopic')
	
	MACS_peak_calling =MACS_callPeak(macs_path, bed_path, name, outdir, genome_size, input_format=input_format, shift=shift, ext_size=ext_size, keep_dup = keep_dup, q_value = q_value)
	log.info(name + ' done!')
	return MACS_peak_calling
	

class MACS_callPeak():
	"""
	Parameters
	---------
	macs_path: str
		Path to MACS binary (e.g. /xxx/MACS/xxx/bin/macs2).
	bed_path: str
		Path to fragments file bed file.
	name: str
		Name of string of the group.
	outdir: str
		Path to the output directory. 
	genome_size: str
		Effective genome size which is defined as the genome size which can be sequenced. Possible values: 'hs', 'mm', 'ce' and 'dm'.
	input_format: str
		Format of tag file can be ELAND, BED, ELANDMULTI, ELANDEXPORT, SAM, BAM, BOWTIE, BAMPE, or BEDPE. Default is AUTO which will
		allow MACS to decide the format automatically. Default: 'BEDPE'.
	shift: int
		To set an arbitrary shift in bp. For finding enriched cutting sites (such as in ATAC-seq) a shift of 73 bp is recommended.
		Default: 73.
	ext_size: int
		To extend reads in 5'->3' direction to fix-sized fragment. For ATAC-seq data, a extension of 146 bp is recommended. 
		Default: 146.
	keep_dup: str
		Whether to keep duplicate tags at te exact same location. Default: 'all'.
	q_value: float
		The q-value (minimum FDR) cutoff to call significant regions. Default: 0.05.
	"""
	def __init__(self,
				 macs_path: str,
				 bed_path: str,
				 name: str,
				 outdir: str,
				 genome_size: str,
				 input_format: Optional[str] = 'BEDPE',
				 shift: Optional[int] = 73,
				 ext_size: Optional[int] = 146, 
				 keep_dup: Optional[str] = 'all',
				 q_value: Optional[int] = 0.05):
		self.macs_path = macs_path
		self.treatment = bed_path
		self.name = name
		self.outdir = outdir
		self.format = input_format
		self.gsize = genome_size
		self.shift = shift
		self.ext_size = ext_size
		self.keep_dup = keep_dup
		self.qvalue = q_value
		self.callpeak()

	def callpeak(self):
		"""
		Run MACS2 peak calling.
		"""
		# Create logger
		level	= logging.INFO
		format   = '%(asctime)s %(name)-12s %(levelname)-8s %(message)s'
		handlers = [logging.StreamHandler(stream=sys.stdout)]
		logging.basicConfig(level = level, format = format, handlers = handlers)
		log = logging.getLogger('cisTopic')
		
		cmd = self.macs_path + ' callpeak --treatment %s --name %s  --outdir %s --format %s --gsize %s '\
			'--qvalue %s --nomodel --shift %s --extsize %s --keep-dup %s --call-summits --nolambda'

		cmd = cmd % (
			self.treatment, self.name, self.outdir, self.format, self.gsize,
			self.qvalue, self.shift, self.ext_size, self.keep_dup
		)
		log.info("Calling peaks for " + self.name + " with %s", cmd)
		try:
			subprocess.check_output(args=cmd, shell=True, stderr=subprocess.STDOUT)
		except subprocess.CalledProcessError as e:
			raise RuntimeError("command '{}' return with error (code {}): {}".format(e.cmd, e.returncode, e.output))
		self.narrow_peak = self.load_narrow_peak()
		
	def load_narrow_peak(self):
		"""
		Load MACS2 narrow peak files as :class:`pr.PyRanges`.
		"""
		narrow_peak = pd.read_csv(self.outdir + self.name + '_peaks.narrowPeak', sep='\t', header = None)
		narrow_peak.columns = ['Chromosome', 'Start', 'End', 'Name', 'Score', 'Strand', 'FC_summit', '-log10_pval', '-log10_qval', 'Summit']
		narrow_peak_pr = pr.PyRanges(narrow_peak)
		return narrow_peak_pr
