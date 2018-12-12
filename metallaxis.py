#!/usr/bin/env python3
# -*- coding: utf-8 -*-

## Importer des modules de Python Standard
import sys
import re
import os
import tracemalloc
import json
import requests
# pour gerer les VCF compressé
import lzma
import bz2
import gzip

import pathlib  # for making the folder where we store data
import platform  # for determining OS and therefore where to store data

# Importer les modules de tierce-partie
import magic  # pour detecter type de fichier
import pandas as pd
# pour lire uniquement certains lignes des fichiers (reduit conso RAM)
from itertools import islice
from PyQt5 import QtCore, QtGui, QtWidgets, uic
from PyQt5.QtCore import QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QApplication, QMainWindow, QDialog, QMessageBox, QProgressBar, QTableWidget, QLabel, QDesktopWidget

import time
start_time = time.time()

# allow <Ctrl-c> to terminate the GUI
import signal
signal.signal(signal.SIGINT, signal.SIG_DFL)

# Determine where to store Metallaxis Data
home_dir = os.path.expanduser('~')
if platform.system() == "Linux":
	working_directory = home_dir + "/.metallaxis/"
elif platform.system() == "Darwin":
	working_directory = home_dir + "/Library/Caches/Metallaxis/"
elif platform.system() == "Windows":
	working_directory = os.path.expandvars(r'%APPDATA%\Metallaxis\\')

# make data folder and parent folders if they don't exist
pathlib.Path(working_directory).mkdir(parents=True, exist_ok=True)

# Tempory file names
global h5_output_name, annotated_h5_output_name, decompressed_vcf_output
h5_output_name = os.path.join(working_directory, 'input.h5')
annotated_h5_output_name = os.path.join(
	working_directory, 'input_annotated.h5')
decompressed_vcf_output = os.path.join(
	working_directory, 'decompressed_vcf_output.vcf')

def decompress_vcf(compression_type, selected_vcf, headonly):
	"""
	Décompresse le fichier d'entrée (si compressé avec with xz/gz/bz2), et
	retourne un objet exploitable: soit le head du fichier, soit le fichier
	entier selon l'option headonly.
	"""
	# la commande open() ouvre juste le fichier, il ne le charge pas en
	# entier ou même de tout en RAM. Ça c'est fait quand on la lis en
	# list plus bas, où on ne lira que les 100 premiers lignes pour eviter
	# de charger de très gros fichiers en RAM
	if compression_type == "":
		decompressed_file_object = open(selected_vcf, mode="rb")
	else:
		decompressed_file_object = eval(compression_type).open(selected_vcf, mode="rb")

	if headonly is True:
		decompressed_file_head = list(islice(decompressed_file_object, 100))
		decompressed_file_object.close()
		return decompressed_file_head
	else:
		with open(decompressed_vcf_output, "wb") as decompressed_out:
			decompressed_out.write(decompressed_file_object.read())
		decompressed_file_object.close()
		return decompressed_file_object



# Charge l'interface graphique construit en XML
gui_window_object, gui_base_object = uic.loadUiType("MetallaxisGui.ui")


class MetallaxisGui(gui_base_object, gui_window_object):
	"""
	Classe qui construit l'interface graphique Qt sur lequel repose Metallaxis
	"""
	def __init__(self):
		super(gui_base_object, self).__init__()
		self.setupUi(self)
		self.setWindowTitle("Metallaxis")
		# initalise progress bar
		self.MetallaxisProgress = MetallaxisProgress()
		self.MetallaxisProgress.show()

		# Center GUI on screen
		qtRectangle = self.frameGeometry()
		centerPoint = QDesktopWidget().availableGeometry().center()
		qtRectangle.moveCenter(centerPoint)

		# start mesuring memory
		tracemalloc.start()
		global normal_mem
		normal_mem = tracemalloc.take_snapshot()


		self.progress_bar(1,"setting up gui")
		# boutons sur interface
		self.open_vcf_button.clicked.connect(self.select_and_process)
		# menus sur interface
		self.actionOpen_VCF.triggered.connect(self.select_and_process)
		self.actionQuit.triggered.connect(self.close)

		# Relier bouton "Github Page" du menu avec l'URL
		def open_github(url):
			url = "https://github.com/SL-LAIDLAW/Metallaxis"
			QDesktopServices.openUrl(QtCore.QUrl(url))

		self.actionGithub_Page.triggered.connect(open_github)

		def open_about_tab():
			self.tabWidget.setCurrentIndex(3)
		# set first tab as default
		self.tabWidget.setCurrentIndex(0)
		# on click "About", open about tab
		self.actionAbout.triggered.connect(open_about_tab)

		self.MetallaxisSettings = MetallaxisSettings()
		def show_settings_window():
			self.MetallaxisSettings.show()

		self.actionSettings.triggered.connect(show_settings_window)
		self.MetallaxisSettings.annotate_species_comboBox.addItems(['Other', 'Human'])

		# convert gui settings to global variables
		global complevel, complib, h5chunksize
		complevel = self.MetallaxisSettings.compression_level_spinBox.text()
		complib = self.MetallaxisSettings.compression_comboBox.currentText()
		h5chunksize = self.MetallaxisSettings.vcf_chunk_size.text()

		# on changing Species combobox in Settings, run the changed_species_combobox
		# function that'll enable or disable the "annotation" checkbox
		self.MetallaxisSettings.annotate_species_comboBox.currentTextChanged.connect(self.changed_species_combobox)

		# obtenir vcf d'entrée
		self.progress_bar(2,"Parsing arguments")
		h5_only = False
		if len(sys.argv) == 1:
			selected_vcf = self.select_vcf()
			metadata_dict, var_counts = self.process_vcf(selected_vcf)
			h5_file = self.h5_encode(selected_vcf, var_counts=var_counts, metadata_dict=metadata_dict)
			# Read H5 for actual table populating
			complete_h5_file = pd.read_hdf(h5_file, key="df",)
		elif len(sys.argv) == 2:
			if sys.argv[1].endswith(".h5"):
				# return error if h5 doesn't exist
				if not os.path.isfile(sys.argv[1]):
					self.throw_error_message("ERROR: Selected file does not \
				exist. You specified : " + str(sys.argv[1]))
					return
				try:
					complete_h5_file  = pd.read_hdf(sys.argv[1])
				except ValueError:
					complete_h5_file  = pd.read_hdf(sys.argv[1], key="df")

				self.loaded_vcf_lineedit.setText(os.path.abspath(sys.argv[1]))
				h5_only = True
				global h5_input_name
				h5_input_name = sys.argv[1]
			else:
				# obtenir le chemin absolue afin d'être dans les memes conditions
				# que si on le selectionnait
				selected_vcf = os.path.abspath(sys.argv[1])
				metadata_dict, var_counts = self.process_vcf(selected_vcf)
				h5_file = self.h5_encode(selected_vcf, var_counts=var_counts, metadata_dict=metadata_dict)
				# Read H5 for actual table populating
				complete_h5_file = pd.read_hdf(h5_file,key="df")
		else:
			print("Error: Metallaxis can only take one argument, a vcf file")
			exit(1)

		if h5_only:
			self.post_h5_processing(complete_h5_file, h5_only)
		else:
			self.post_h5_processing(complete_h5_file, h5_only, var_counts=var_counts, metadata_dict=metadata_dict)

	def post_h5_processing(self, complete_h5_file, h5_only, var_counts=None, metadata_dict=None, selected_vcf=None):
		"""
		function that clears the interface if it already has data,
		then runs the annotate_h5() populate_table() functions. Requires h5
		read object, h5 only boolean and optionally takes selected_vcf string
		if annotation is chosen.
		"""
		# active les widgets qui sont desactivés tant qu'on a pas de VCF selectioné
		self.loaded_vcf_lineedit.setEnabled(True)
		self.loaded_vcf_label.setEnabled(True)
		self.meta_detected_filetype_label.setEnabled(True)
		self.metadata_area_label.setEnabled(True)
		self.viewer_tab_table_widget.setEnabled(True)
		self.filter_table_btn.setEnabled(True)
		self.filter_label.setEnabled(True)
		self.filter_lineedit.setEnabled(True)
		self.filter_box.setEnabled(True)

		# get column numbers for ID, POS, etc.
		self.progress_bar(47,"Extracting column data")
		global column_names
		column_names = list(complete_h5_file.keys())

		global chrom_col, id_col, pos_col, ref_col, alt_col, qual_col
		chrom_col = [i for i, s in enumerate(column_names) if 'CHROM' in s][0]
		id_col = [i for i, s in enumerate(column_names) if 'ID' in s][0]
		pos_col = [i for i, s in enumerate(column_names) if 'POS' in s][0]
		ref_col = [i for i, s in enumerate(column_names) if 'REF' in s][0]
		alt_col = [i for i, s in enumerate(column_names) if 'ALT' in s][0]
		qual_col = [i for i, s in enumerate(column_names) if 'QUAL' in s][0]

		# effacer espace metadonées (utile si on charge un fichier apres un autre)
		self.empty_qt_layout(self.dynamic_metadata_label_results)
		self.empty_qt_layout(self.dynamic_metadata_label_tags)
		# effacer aussi espace statistiques
		self.empty_qt_layout(self.dynamic_stats_value_label)
		self.empty_qt_layout(self.dynamic_stats_key_label)

		# effacer chrom_filter_box
		self.filter_box.clear()
		self.filter_text.setText(" ")

		# set filter_box to list column_names
		self.filter_box.addItems(column_names)
		self.filter_table_btn.clicked.connect(self.filter_table)

		if not h5_only:
			for metadata_line_nb in metadata_dict:
				metadata_tag = metadata_dict[metadata_line_nb][1]
				metadata_result = metadata_dict[metadata_line_nb][2]
				if not metadata_tag.isupper():
					# Generer dynamiquement du texte pour le titre et resultat pour
					# chaque type de metadonnée non-majiscule
					self.dynamic_metadata_label_tags.addWidget(
						QtWidgets.QLabel(metadata_tag, self))
					self.dynamic_metadata_label_results.addWidget(
						QtWidgets.QLabel(metadata_result, self))

			for key, value in var_counts.items():
				key=key.replace("_"," ")
				key=key+":"
				self.dynamic_stats_key_label.addWidget(
					QtWidgets.QLabel(str(key), self))
				self.dynamic_stats_value_label.addWidget(
					QtWidgets.QLabel(str(value), self))

		else:
			for line in pd.read_hdf(h5_input_name , key="metadata").itertuples():
				# turn tuple into a list, but exclude the first item of list
				# because its the h5 index and not part of our original data
				line = list(line)[1:]
				metadata_tag = line[0]
				metadata_result = line[1]
				self.dynamic_metadata_label_tags.addWidget(
					QtWidgets.QLabel(metadata_tag, self))
				self.dynamic_metadata_label_results.addWidget(
					QtWidgets.QLabel(metadata_result, self))

			for line in pd.read_hdf(h5_input_name , key="stats").itertuples():
				var_counts_key = line[0]
				var_counts_value = line[1]
				self.dynamic_stats_key_label.addWidget(
					QtWidgets.QLabel(str(var_counts_key), self))
				self.dynamic_stats_value_label.addWidget(
					QtWidgets.QLabel(str(var_counts_value), self))


		if self.MetallaxisSettings.annotation_checkbox.isChecked():
			if h5_only is not True:
				complete_h5_file = self.annotate_h5(complete_h5_file, selected_vcf)
				complete_h5_file = pd.read_hdf(complete_h5_file, key="df", where="ID!='.'")


		self.populate_table(complete_h5_file)


	def throw_warning_message(self, warning_message):
		"""
		Generer une dialogue d'avertissement avec l'argument comme message
		"""
		print("Warning: " + warning_message)
		warning_dialog = QtWidgets.QMessageBox()
		warning_dialog.setIcon(QMessageBox.Warning)
		warning_dialog.setWindowTitle("Warning:")
		warning_dialog.setText(warning_message)
		warning_dialog.setStandardButtons(QMessageBox.Ok)
		warning_dialog.exec_()

	def throw_error_message(self, error_message):
		"""
		Generer une dialogue d'alerte avec l'argument comme message d'alerte
		"""
		print("Error: " + error_message)
		error_dialog = QtWidgets.QMessageBox()
		error_dialog.setIcon(QMessageBox.Critical)
		error_dialog.setWindowTitle("Error!")
		error_dialog.setText(error_message)
		error_dialog.setStandardButtons(QMessageBox.Ok)
		error_dialog.exec_()

	def empty_qt_layout(self, qt_layout_name):
		"""
		Vide le Qt layout afin qu'on puisse changer de fichier VCF sans
		garder l'information du dernier sur l'interface.

		Accepte le nom d'un Qt layout comme argument.
		"""
		while 1:
			layout_widget = qt_layout_name.takeAt(0)
			if not layout_widget:
				break
			layout_widget.widget().deleteLater()

	def filter_table(self):
		"""
		Selectionne les données correspondant au filtre choisi, et les envoient
		au fonction populate_table pour remplacer les lignes du tableau avec
		uniquement les données qui correspondent à notre filtre.
		"""
		selected_filter = self.filter_box.currentText()
		filter_text = self.filter_lineedit.text()
		# enleve espace blanc de la requete
		filter_text = re.sub(r"\s+", "", filter_text)
		filter_text = filter_text.upper()

		if "-" in filter_text and "," in filter_text:
			self.throw_error_message("Please only use either comma separated values or a dash separated range")
			return

		elif "-" in filter_text:
			if selected_filter not in numeric_filters:
				self.throw_error_message("Can only filter a dash separated range on numeric columns: " + str(numeric_filters))
				return

			split_filter_text = filter_text.split("-")
			# filtrer les valeurs nulles ou strings vides pour pas gener le comptage des item
			split_filter_text = filter(None, split_filter_text)
			split_filter_text = list(split_filter_text )
			if len(split_filter_text) == 2:
				self.filter_text.setText("Filtering to show " + selected_filter + " from "+ str(split_filter_text[0]) + " to " + str(split_filter_text[1]))

				if split_filter_text[0] > split_filter_text[1]:
					filter_condition = selected_filter + ">=" + split_filter_text[1] + " & " + selected_filter + "=<" + split_filter_text[0]
				elif split_filter_text[0] < split_filter_text[1]:
					filter_condition = selected_filter + ">=" + split_filter_text[0] + " & " + selected_filter + "=<" + split_filter_text[1]
				else:
					filter_condition = selected_filter + "==" + split_filter_text[0]

				if self.MetallaxisSettings.annotation_checkbox.isChecked():
					filtered_h5_table = pd.read_hdf(annotated_h5_output_name, key="df", where=filter_condition)
				else:
					filtered_h5_table = pd.read_hdf(h5_output_name, key="df", where=filter_condition)
			else:
				self.throw_error_message("Please only enter 2 values separated by a dash")
				return

		elif "," in filter_text:
			split_filter_text = filter_text.split(",")
			# filtrer les valeurs nulles ou strings vides pour pas gener le comptage des item
			split_filter_text  = filter(None, split_filter_text)
			split_filter_text  = list(split_filter_text)
			nb_filters = len(split_filter_text)
			if nb_filters >= 1:
				self.filter_text.setText("Filtering to show "  + selected_filter + ": " + str(split_filter_text))
				filter_condition = selected_filter + " in " + str(split_filter_text)

				if self.MetallaxisSettings.annotation_checkbox.isChecked():
					filtered_h5_table = pd.read_hdf(annotated_h5_output_name, key="df", where=filter_condition)
				else:
					filtered_h5_table = pd.read_hdf(h5_output_name, key="df", where=filter_condition)

			else:
				self.filter_text.setText(" ")
				self.throw_error_message("Please enter 2 or more values separated by a comma")
				return

		elif filter_text == "":
			self.filter_text.setText("No Filter Selected")
			if self.MetallaxisSettings.annotation_checkbox.isChecked():
				filtered_h5_table = pd.read_hdf(annotated_h5_output_name, key="df")
			else:
				filtered_h5_table = pd.read_hdf(h5_output_name, key="df")
		else:
			self.filter_text.setText("Filtering to show " + selected_filter + ": " + str(filter_text))
			filter_condition = selected_filter+"==\""+filter_text+"\""
			if self.MetallaxisSettings.annotation_checkbox.isChecked():
				filtered_h5_table = pd.read_hdf(annotated_h5_output_name, key="df", where=filter_condition)
			else:
				filtered_h5_table = pd.read_hdf(h5_output_name, key="df", where=filter_condition)

		self.populate_table(filtered_h5_table)


	def verify_file(self,selected_vcf):
		"""
		Verifer si le fichier donnée existe, et n'est pas vide.
		Retourne True si selected_vcf est un fichier valide.
		"""
		self.progress_bar(3,"Verifying VCF: verifying that file is valid")

		# verifier que le fichier existe
		if not os.path.isfile(selected_vcf):
			self.throw_error_message("ERROR: Selected file does not \
		exist. You specified : " + str(selected_vcf))
			return False

		# verifier que le fichier n'est pas vide
		if not os.path.getsize(selected_vcf) > 0:
			self.throw_error_message("ERROR: Selected file is empty. \
		You specified : " + str(selected_vcf))
			return False

		# retourne True pour continuer
		return True


	def verify_vcf(self,decompressed_file_head):
		self.progress_bar(8,"Verifying VCF: verifying that VCF is valid")
		# verify is conform to VCFv4.1 specification:
		# The header line names the 8 fixed, mandatory columns. These columns are as follows:
		# CHROM
		# POS
		# - (Integer, Required)
		# ID
		# - No identifier should be present in more than one data record
		# REF
		# - must be one of A,C,G,T,N (case insensitive). Multiple bases are permitted
		# ALT
		# - must be one of A,C,G,T,N (case insensitive). Multiple bases are permitted
		# - or an angle-bracketed ID String (“<ID>”)
		# - or a breakend replacement string as described in the section on
		# breakends.
		# - If there are no alternative alleles, then the missing value should be used.
		# QUAL
		# - float or Integer
		# FILTER
		# INFO

		# also verify no lines start with a # after end of header
		line_num = 0
		variant_num = 0
		global metadata_num, qual_is_numeric
		qual_is_numeric = True
		for line in decompressed_file_head:
			line_num = line_num + 1
			if line.startswith(b'#'):
				# isoler le ligne du header avec les colonnes
				if line.startswith(b'#CHROM'):
					# verify header is conform to vcf 4.1 spec
					# decode byte object to utf-8 string and split by tab
					header_line_cols = line.decode('UTF-8').split("\t")
					# get index of each column
					global chrom_col, id_col, pos_col, ref_col, alt_col, qual_col
					chrom_col = [i for i, s in enumerate(header_line_cols) if '#CHROM' in s][0]
					id_col = [i for i, s in enumerate(header_line_cols) if 'ID' in s][0]
					pos_col = [i for i, s in enumerate(header_line_cols) if 'POS' in s][0]
					ref_col = [i for i, s in enumerate(header_line_cols) if 'REF' in s][0]
					alt_col = [i for i, s in enumerate(header_line_cols) if 'ALT' in s][0]
					qual_col = [i for i, s in enumerate(header_line_cols) if 'QUAL' in s][0]
					# verifier que l'entete contient tous les colonnes obligatoires du VCF 4.1
					if not all(x in header_line_cols  for x in ['#CHROM', 'POS', 'REF', 'ALT', 'QUAL', 'FILTER', 'INFO']):
						self.throw_error_message("ERROR: VCF not valid: VCF doesn not contain all required columns")
						return False

			else:
				# if line is not part of header, split by tab to get columns
				# and verify each column for each line is VCFv4.1 conforming
				split_line = line.decode('UTF-8').split("\t")
				for col in split_line:
					col = col.strip()
					# verifier que le pos ne conteint que des chiffres
					if col == split_line[pos_col]:
						if not col.isdigit():
							self.throw_error_message("ERROR: VCF not valid: column 'POS' doesn't only contain digits: " + str(col))
							return False

					elif col == split_line[ref_col]:
						allowed_chars = set('ACGTN')
						# verifier que REF ne contient pas autre chose que ACGTN
						# (les seules characters authorisés selon VCF 4.1)
						if not set(col.upper()).issubset(allowed_chars):
							self.throw_error_message("ERROR: VCF not valid: column 'REF' doesn't only contain A,C,G,T,N: " + str(col))
							return False

					elif col == split_line[alt_col]:
						# verifier que ALT ne contient pas autre chose que ACGTN
						# ou un identifiant entre <> (les seules characters
						# authorisés selon VCF 4.1)
						if not col.startswith("<") and col.endswith(">"):
							allowed_chars = set('ACGTN')
							if not set(col).issubset(allowed_chars):
								self.throw_error_message("ERROR: VCF not valid: column 'ALT' doesn't only contain A,C,G,T,N or <ID>: " + str(col))
								return False

					elif col == split_line[qual_col]:
						# verifier que le QUAL ne contient que un entier
						# ou un float ou un "."
						if col.isdigit():
							break
						elif col == ".":
							qual_is_numeric = False
							break
						else:
							allowed_chars = set('123456789.')
							if not set(col).issubset(allowed_chars):
								try:
									float(col)

								except ValueError:
									self.throw_error_message("ERROR: VCF not valid: column 'QUAL' doesn't only contain digits: " + str(col))
									return False
				variant_num += 1

			metadata_num = int(line_num - variant_num)

		if variant_num == 0:
			self.throw_error_message("ERROR: VCF is empty, there are no variants at all in this vcf, please use a different vcf")
			return False
		elif variant_num < 5:
			self.throw_error_message("ERROR: VCF contains too few variants to analyse, please use a different vcf")
			return False
		elif variant_num > 5 and variant_num < 30:
			self.throw_warning_message("Warning: VCF contains very few variants, only rudementary statistics can be perfomred")
			return True
		else:
			# si plus de 35 variants retouner sans alerte
			return True


	def select_vcf(self):
		"""
		Ouvre une dialogue pour que l'utilisateur puisse choisir un fichier
		VCF, et ensuite il le passe à la fonction decompress_vcf()
		"""
		select_dialog = QtWidgets.QFileDialog()
		select_dialog.setAcceptMode(select_dialog.AcceptSave)
		selected_vcf = select_dialog.getOpenFileName(self, filter="VCF Files (*.vcf \
			*.vcf.xz *.vcf.gz *.vcf.bz2) ;;All Files(*.*)")
		selected_vcf = selected_vcf[0]
		# if the user cancels the select_vcf() dialog, then run select again
		while selected_vcf == "":
			self.throw_error_message("ERROR: No selected file")
			selected_vcf = self.select_vcf()
		return selected_vcf


	def process_vcf(self, selected_vcf):
		"""
		Effectue les verifications et analyses sur le VCF choisi
		"""
		# verifier que le fichier est valide, on verra s'il est un
		# vcf valide apres décompression
		file_is_valid = self.verify_file(selected_vcf)
		if not file_is_valid:
			return


		# TODO: replace arg_file_type with something more intuitive like file_type
		arg_file_type = magic.from_file(selected_vcf)
		# Decompresse fichiers selectionées
		if "XZ" in arg_file_type:
			self.detected_filetype_label.setText("xz compressed VCF")
			decompressed_file_head = decompress_vcf("lzma", selected_vcf, headonly=True)

		elif "bzip2" in arg_file_type:
			self.detected_filetype_label.setText("bz2 compressed VCF")
			decompressed_file_head  = decompress_vcf("bz2", selected_vcf, headonly=True)

		elif "gzip" in arg_file_type:
			self.detected_filetype_label.setText("gz compressed VCF")
			decompressed_file_head  = decompress_vcf("gzip", selected_vcf, headonly=True)

		elif "Variant Call Format" in arg_file_type:
			self.detected_filetype_label.setText("uncompressed VCF")
			decompressed_file_head  = decompress_vcf("", selected_vcf, headonly=True)

		else:
			self.throw_error_message("Error: Selected file must be a VCF file")
			return

		# now we have a returned decompressed file object verify if
		# contents are valid vcf
		vcf_is_valid = self.verify_vcf(decompressed_file_head)
		if not vcf_is_valid:
			return


		self.progress_bar(9,"Decompressing VCF")
		# si le vcf est valide alors decompressons tout le fichier
		if "XZ" in arg_file_type:
			decompressed_file= decompress_vcf("lzma", selected_vcf, headonly=False)

		elif "bzip2" in arg_file_type:
			decompressed_file = decompress_vcf("bz2", selected_vcf, headonly=False)

		elif "gzip" in arg_file_type:
			decompressed_file = decompress_vcf("gzip", selected_vcf, headonly=False)

		elif "Variant Call Format" in arg_file_type:
			decompressed_file = decompress_vcf("", selected_vcf, headonly=False)

		self.loaded_vcf_lineedit.setText(os.path.abspath(selected_vcf))

		# Calculate counts of different variant types
		def add_to_dict_iterator(dictionary, key, iterator_value):
			"""
			appends value to list at dictionary[key], but if key doesn't
			already exist then it adds it
			"""
			if key not in dictionary:
				# add empty list to dict as key
				dictionary[key] = 0
				dictionary[key] = dictionary[key] + iterator_value
			else:
				# append value to list
				dictionary[key] = dictionary[key] + iterator_value


		var_counts = {}
		var_counts["Total_SNP_Count"] = 0
		var_counts["Total_Indel_Count"] = 0
		global list_chromosomes
		list_chromosomes = set() # use set instead of list so it won't store duplicate values

		with open(decompressed_vcf_output) as decompressed_out:
			for line in decompressed_out:
				if not line.startswith('#'):
					line = line.split("\t")
					list_chromosomes.add(line[chrom_col])

					if len(line[ref_col]) == len(line[alt_col]):
						var_counts["Total_SNP_Count"] += 1
						add_to_dict_iterator(var_counts, line[chrom_col]+"_Chrom_SNP_Count", 1)
					else:
						var_counts["Total_Indel_Count"] += 1
						add_to_dict_iterator(var_counts, line[chrom_col]+"_Chrom_Indel_Count", 1)
		#TODO: calculate average nombre of mutations per chromosome
		total_chrom_snp_count, total_chrom_indel_count = 0, 0
		for key, value in var_counts.items():
			if "_Chrom_SNP_Count" in key:
				total_chrom_snp_count += value
			if "_Chrom_Indel_Count" in key:
				total_chrom_indel_count += value

		var_counts["Avg_SNP_per_Chrom"] = int(total_chrom_snp_count / len(list_chromosomes))
		var_counts["Avg_Indel_per_Chrom"] = int(total_chrom_indel_count / len(list_chromosomes))


		# Extract Metadata from VCF
		metadata_dict = {}
		# Match des groupes des deux cotés du "=", apres un "##"
		regex_metadata = re.compile('(?<=##)(.*?)=(.*$)')

		self.progress_bar(10,"Extracting VCF metadata")
		# parser vcf decompressé dans plusieurs dictionnaires
		with open(decompressed_vcf_output) as decompressed_out:
			vcf_line_nb, metadata_line_nb = 0, 0
			for line in decompressed_out:
				if line.startswith('##'):
					metadata_tag = str(regex_metadata.search(line).group(1))
					metadata_result = str(regex_metadata.search(line).group(2))
					# dans un premier temps on va pas s'interesser pas aux
					# metadonéées 'en majiscules' ("INFO" "FILTER" "ALT")
					# peuvent être regroupés ensemble dans un tableau
					if metadata_tag.isupper():
						metadata_type = metadata_tag
					else:
						metadata_type = "basic"
						# too long metadata distorts the GUI window and causes
						# database errors so truncate if its too long
						if len(metadata_tag) > 20:
							metadata_tag = metadata_tag[:20] + "..."
						if len(metadata_result) > 95:
							metadata_result = metadata_result[:95] + "...<truncated due to length>"

					metadata_dict_entry = [metadata_type, metadata_tag, metadata_result]
					if not metadata_dict_entry in metadata_dict.values():
						metadata_dict[metadata_line_nb] = metadata_dict_entry
					metadata_line_nb += 1

		return (metadata_dict, var_counts)



	def populate_table(self, selected_h5_data):
		# effacer tableau actuelle
		self.viewer_tab_table_widget.setRowCount(0)
		self.viewer_tab_table_widget.setColumnCount(0)

		# Creer Tableau vide avec bonne nombre de colonnes et lignes
		table_length = selected_h5_data.shape[0]
		table_width = selected_h5_data.shape[1]

		self.viewer_tab_table_widget.setRowCount(table_length)
		self.viewer_tab_table_widget.setColumnCount(table_width)

		self.viewer_tab_table_widget.setHorizontalHeaderLabels(column_names)


		# Remplir Tableau
		vcf_line_nb = 0
		annotate_percent = 80
		for line in selected_h5_data.itertuples():
			if table_length >= 1000:
				# only update progress bar every 300 lines to avoid performance hit
				# from doing it every line
				if vcf_line_nb % 300 == 0:
					annotate_progress = annotate_percent + (vcf_line_nb / table_length) * (100 - annotate_percent)
					self.progress_bar(float(annotate_progress), "Populating Table from H5")

			# turn tuple into a list, but exclude the first item of list
			# because its the h5 index and not part of our original data
			line = list(line)[1:]

			vcf_field_nb = 0
			for vcf_field in line:
				self.viewer_tab_table_widget.setItem(
					vcf_line_nb, vcf_field_nb, QtWidgets.QTableWidgetItem(str(vcf_field)))
				vcf_field_nb += 1
			vcf_line_nb += 1
		self.progress_bar(100,"Populating Table...done")
		# close progress bar when file is completely loaded
		self.MetallaxisProgress.close()



	def select_and_process(self):
		# this method only allows selection of vcfs so set h5_only to false
		h5_only = False
		selected_vcf = self.select_vcf()
		# reopen progress bar for loading new file
		self.MetallaxisProgress = MetallaxisProgress()
		self.MetallaxisProgress.show()
		metadata_dict, var_counts = self.process_vcf(selected_vcf)
		h5_file = self.h5_encode(selected_vcf, var_counts=var_counts, metadata_dict=metadata_dict)
		complete_h5_file = pd.read_hdf(h5_file, key="df",)
		self.post_h5_processing(complete_h5_file, h5_only, var_counts=var_counts, metadata_dict=metadata_dict, selected_vcf=selected_vcf)


	def h5_encode(self, selected_vcf, var_counts=None, metadata_dict=None):
		"""
		Lis en entier le vcf selectionné, bloc par bloc et le met dans un
		fichier HDF5.
		"""
		h5_file = pd.HDFStore(h5_output_name ,mode='w')

		for metadata_line_nb in metadata_dict:
			metadata_tag = str(metadata_dict[metadata_line_nb][1])
			metadata_result = str(metadata_dict[metadata_line_nb][2])
			if not metadata_tag.isupper():
				metadata_line = {'Tag': metadata_tag, 'Result': metadata_result}
				metadata_line = pd.DataFrame(
					metadata_line, index=[metadata_line_nb])
				h5_file.append("metadata", metadata_line, data_columns=True,
							min_itemsize=150, complib=complib, complevel=int(complevel))

		h5_stat_table_index = 0
		for var_counts_key, var_counts_value in var_counts.items():
			var_counts_key = str(var_counts_key)
			var_counts_value = str(var_counts_value)
			if len(var_counts_key) > 40:
				var_counts_key = var_counts_key[:40] + "..."
			if len(var_counts_value) > 60:
				var_counts_value = var_counts_value[:60] + "..."
			var_counts_line = {'Tag': var_counts_key, 'Result': var_counts_value}
			var_counts_line = pd.DataFrame(
				var_counts_line, index=[h5_stat_table_index])

			h5_file.append("stats", var_counts_line, data_columns=True,
						min_itemsize=100, complib=complib, complevel=int(complevel))
			h5_stat_table_index += 1

		chunked_vcf_len = sum(1 for row in open(selected_vcf, 'r'))
		chunked_vcf = pd.read_csv(selected_vcf,
								delim_whitespace=True,
								skiprows=range(0, metadata_num-1),
								chunksize=int(h5chunksize),
								low_memory=False,
								# make default data type an object (ie. string)
								dtype=object)
		annotate_nb = 0
		annotate_percent = 35


		# PARSE INFO COLUMNS
		# the info column of a vcf is long and hard to read if
		# displayed as is, but it is composed of multiple key:value tags
		# that we can parse as new columns, making more visual sense
		global info_cols_to_add
		info_cols_to_add = set()
		chunked_vcf = pd.read_csv(selected_vcf,
								delim_whitespace=True,
								skiprows=range(0, metadata_num-1),
								chunksize=int(h5chunksize),
								low_memory=False,
								# make default data type an object (ie. string)
								dtype=object)

		# Run through every chunk of the whole VCF and get the key out of the
		# key:value pair and add to a set (so no duplicates will be added) that
		# will later become new column names
		for chunk in chunked_vcf:
			for line in chunk["INFO"]:
				if ";" in line:
					line_split = line.split(";")
					for col in line_split:
						col_to_add = col.split("=")[0]
						info_cols_to_add.add(col_to_add)


		global table_column_names
		chunked_vcf = pd.read_csv(selected_vcf,
								delim_whitespace=True,
								skiprows=range(0, metadata_num-1),
								chunksize=int(h5chunksize),
								low_memory=False,
								# make default data type an object (ie. string)
								dtype=object)
		for chunk in chunked_vcf:
			# set the new info column names to be empty by default
			for col in info_cols_to_add:
				chunk[col] = "."

			line_nb = 0
			for line in chunk["INFO"]:
				# split the INFO column by ; which separates the different
				# key:value pairs
				if ";" in line:
					line_split = line.split(";")
					# get both sides of the = to get the key and the value
					for col in line_split:
						col_split = col.split("=")
						key_to_add = col_split[0]
						# col_split will be greater than 1 if there is an = sign
						# a = means there is a key:value pair to be extracted
						if len(col_split) > 1:
							data_to_add = col_split[1]
							chunk[key_to_add].values[line_nb] = data_to_add
						# if there is no = sign then there is no key:value pair just
						# a tag so set it tag as a boolean column
						else:
							chunk[key_to_add].values[line_nb] = "T"
				line_nb += 1

			# Rename column so we get 'CHROM' not '#CHROM' from chunk.keys()
			chunk.rename(columns={'#CHROM': 'CHROM'}, inplace=True)

			# set global variable table_column_names to have both normal table
			# columns and the parsed columns from INFO column
			table_column_names = list(chunk.keys()) + list(info_cols_to_add)

			# SET COLS WITH NUMBERS TO BE NUMERIC TYPE
			# set columns that only contain numbers to be numeric dtype
			# otherwise they are just strings, and can't be used with a
			# dash separated filter

			# make a list from all column names, we will later remove the
			# non-numeric columns from the list
			numeric_columns = table_column_names


			def set_col_to_numeric_if_isdigit(column , chunk):
				# chunk[col_name] = chunk[col_name].astype(str)
				# the default isdigit() doesnt recognise floats so write our own
				def is_number_bool(sample):
					try:
						float(sample)
					except:
						return False
					return True

				def del_col(column):
					if column in numeric_columns:
						numeric_columns.remove(column)

				for row in chunk[column]:
					row = str(row)
					if "," in row:
						del_col(column)
					if ";" in row:
						del_col(column)
					if "|" in row:
						del_col(column)
					if bool(re.match('^[0-9]+$',row)) is False:
						if is_number_bool(row) is False:
							if column in numeric_columns:
								numeric_columns.remove(column)


			# run the function for every column to remove non-numeric columns
			# from "numeric_columns" list
			for column in chunk.keys():
				set_col_to_numeric_if_isdigit(column,chunk)

			print(numeric_columns)
			# convert the remaining columns in "numeric_columns" list to numeric datatype
			for column in numeric_columns:
				chunk[column] = pd.to_numeric(chunk[column])


			# only update progress bar every 20 lines to avoid performance hit
			# of refreshing the whole GUI at every line
			if annotate_nb % 20 == 0:
				annotate_progress = annotate_percent + \
					(annotate_nb / chunked_vcf_len) * (43 - annotate_percent)
				self.progress_bar(
					float(annotate_progress), "Encoding H5 database")

			# append the modified chunk to the h5 database without indexing
			h5_file.append("df", chunk, index=False, data_columns=True,
						min_itemsize=80, complib=complib, complevel=int(complevel))
			annotate_nb += 1

		# index columns explicitly, now that we have finished adding data to it
		self.progress_bar(46, "Indexing H5 database")
		h5_file.create_table_index(
			"df", columns=table_column_names, optlevel=9, kind='full')
		h5_file.close()
		return h5_output_name

	def changed_species_combobox(self):
		"""
		Deactivates checkbox for VCF annotation if a species other than 'Human'
		is selected, as only human VCFs can be annotated without changing EBI
		VEP API settings.
		"""
		vcf_species = self.MetallaxisSettings.annotate_species_comboBox.currentText()
		if vcf_species != 'Human':
			self.MetallaxisSettings.annotation_checkbox.setChecked(False)
			self.MetallaxisSettings.annotation_checkbox.setEnabled(False)
			self.MetallaxisSettings.annotate_vcf_label.setEnabled(False)
		else:
			self.MetallaxisSettings.annotation_checkbox.setEnabled(True)
			self.MetallaxisSettings.annotate_vcf_label.setEnabled(True)




	def annotate_h5(self, selected_h5_data, selected_vcf):
		"""
		Uses EBI's VEP API to annotate lines where ID is valid
		"""
		ebi_rest_api = "https://rest.ensembl.org"
		ext = "/vep/human/id/"
		headers = {"Content-Type": "application/json", "Accept": "application/json"}

		api_ids = []
		selected_h5_data = pd.read_hdf(h5_output_name, key="df", where="ID!='.'")

		for line in selected_h5_data.itertuples():
			# turn tuple into a list, but exclude the first item of list
			# because its the h5 index and not part of our original data
			line = list(line)[1:]
			# get the id from id_column and add it to api_ids list
			line_id = line[id_col]
			api_ids.append(line_id)

		annotated_h5 = pd.HDFStore(annotated_h5_output_name ,mode='w')
		annotated_row_ids = []

		# divide api_ids list into sublist as api has a max length
		annotate_nb = 0
		annotate_percent = 55
		for i in range(0, len(api_ids), 50):
			api_ids_sublist = api_ids[i:i + 50]

			formatted_api_ids_sublist = ','.join('"{0}"'.format(ind_id)
				for ind_id in api_ids_sublist)
			formatted_api_ids_sublist = '{ "ids" : [' + \
				formatted_api_ids_sublist + '] }'
			api_call = requests.post(ebi_rest_api + ext, headers=headers,
				data=formatted_api_ids_sublist)


			if not api_call.ok:
				self.throw_error_message("ERROR: API call failed")
				api_call.raise_for_status()
				return

			vep_json = json.loads(api_call.text)


			for data in vep_json:
				# if returned JSON has a transcript_consequence extract its
				# data and add to h5 file
				if data.get('transcript_consequences'):
					for subdata in data['transcript_consequences']:
						# only update progress bar every 20 lines to avoid performance hit
						# from doing it every line
						if annotate_nb % 20 == 0:
							annotate_len = (len(api_ids) * len(data))
							annotate_progress = annotate_percent + (annotate_nb / annotate_len ) * (70 - annotate_percent)
							self.progress_bar(float(annotate_progress), "Annotate H5: writing annotated chunk to h5")
						# get h5 row from id and add JSON data to it and add it to h5
						api_id = data['id']
						api_alt = subdata['variant_allele']

						selected_h5_row = pd.read_hdf(h5_output_name, key="df", where=("ID=='" + api_id + "' and ALT=='" + api_alt + "'"))
						selected_h5_row['IMPACT'] = str(subdata['impact'])
						# show upto 3 consequence terms
						if len(subdata['consequence_terms']) >= 3:
							selected_h5_row['CONSEQUENCE_TERMS_3'] = str(subdata['consequence_terms'][2])
						else:
							selected_h5_row['CONSEQUENCE_TERMS_3'] = "."
						if len(subdata['consequence_terms']) >= 2:
							selected_h5_row['CONSEQUENCE_TERMS_2'] = str(subdata['consequence_terms'][1])
						else:
							selected_h5_row['CONSEQUENCE_TERMS_3'] = "."
						selected_h5_row['CONSEQUENCE_TERMS_1'] = str(subdata['consequence_terms'][0])
						selected_h5_row['GENE_ID'] = str(subdata['gene_id'])
						selected_h5_row['GENE_SYMBOL'] = str(subdata['gene_symbol'])
						selected_h5_row['BIOTYPE'] = str(subdata['biotype'])
						annotated_h5.append('df', selected_h5_row, index=False, data_columns=True, min_itemsize=80,complib=complib,complevel=int(complevel))
						annotated_row_ids.append(api_id)
						annotate_nb += 1

		chunked_vcf_len = sum(1 for row in open(selected_vcf,'r'))
		chunked_vcf = pd.read_csv(selected_vcf,
							delim_whitespace=True,
							skiprows= range(0,metadata_num-1),
							chunksize=1,
							low_memory=False,
							dtype=object, # make default data type an object (ie. string)
							)

		annotate_nb = 0
		annotate_percent = 70
		for chunk in chunked_vcf:
			# Rename Columns
			chunk.rename(columns = {'#CHROM':'CHROM'}, inplace = True)
			chunk_id = str(chunk['ID'].iloc[0])
			if chunk_id != ".":
				if chunk_id not in annotated_row_ids:
					chunk['IMPACT']="."
					chunk['GENE_SYMBOL']="."
					chunk['CONSEQUENCE_TERMS_1']="."
					chunk['CONSEQUENCE_TERMS_2']="."
					chunk['CONSEQUENCE_TERMS_3']="."
					chunk['GENE_ID']="."
					chunk['BIOTYPE']="."
					annotated_h5.append('df',chunk, index=False, data_columns=True, min_itemsize=80,complib=complib,complevel=int(complevel))
					# only update progress bar every 20 lines to avoid
					# performance hit from doing it every line
					if annotate_nb % 20 == 0:
						annotate_progress = annotate_percent + (annotate_nb / chunked_vcf_len) * (80 - annotate_percent)
						self.progress_bar(float(annotate_progress), "Annotate H5: writing non-annotated chunk to h5")
					annotate_nb += 1

		self.progress_bar(80,"Indexing annotated H5")
		cols_to_index = ['CHROM', 'POS', 'ID', 'REF', 'ALT', 'QUAL', 'FILTER', 'IMPACT', 'GENE_SYMBOL', 'CONSEQUENCE_TERMS_1', 'CONSEQUENCE_TERMS_2', 'CONSEQUENCE_TERMS_3', 'GENE_ID', 'BIOTYPE']
		annotated_h5.create_table_index('df', columns=cols_to_index, optlevel=9, kind='full')
		annotated_h5.close()
		return annotated_h5_output_name


	def progress_bar(self,percent,message):
		"""docstring for progress_bar"""
		self.MetallaxisProgress.progressbar_message.setText(message)
		percent = round(percent, 2)
		self.MetallaxisProgress.progressbar_progress.setValue(percent)

		snapshot2 = tracemalloc.take_snapshot()
		mem_usage = sum(stat.size for stat in snapshot2.statistics('lineno'))
		mem_usage_mb = round(mem_usage/1000000, 2)
		mem_usage_mb =("%s" % (mem_usage_mb))

		time_secs = str(round((time.time() - start_time),2))

		self.MetallaxisProgress.progressbar_ram_usage.setText(mem_usage_mb)
		self.MetallaxisProgress.progressbar_time.setText(time_secs)
		print(str(percent) + "% : " + message + " | Time: "+ time_secs + " \
		| RAM (MB): " + mem_usage_mb)
		MetallaxisApp.processEvents()  # refresh the gui



progress_window_object, progress_base_object = uic.loadUiType("MetallaxisProgress.ui")
class MetallaxisProgress(progress_base_object, progress_window_object):
	"""
	Status bar that shows progress when opening files with Metallaxis
	"""
	def __init__(self):
		super(progress_base_object, self).__init__()
		self.setupUi(self)
		self.setWindowTitle("Metallaxis")


settings_window_object, settings_base_object = uic.loadUiType("MetallaxisSettings.ui")
class MetallaxisSettings(settings_base_object, settings_window_object):
	"""
	La fenetre de paramètres qui permet de modifier les paramètres de defaut
	"""
	def __init__(self):
		super(settings_base_object, self).__init__()
		self.setupUi(self)
		self.setWindowTitle("Metallaxis Settings")
		self.compression_comboBox.addItems(['zlib', 'blosc', 'bzip2', 'lzo'])
		self.vcf_chunk_size.setText("5000")
		# Center settings pannel on screen
		qtRectangle = self.frameGeometry()
		centerPoint = QDesktopWidget().availableGeometry().center()
		qtRectangle.moveCenter(centerPoint)


# Si le script est executé directement, lance l'interface graphique
if __name__ == '__main__':
	MetallaxisApp = QApplication(sys.argv)
	MetallaxisGui_object = MetallaxisGui()
	MetallaxisGui_object.show()
	sys.exit(MetallaxisApp.exec_())
