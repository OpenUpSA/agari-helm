/**
 * Gets file type based on file extension
 * @param {string} fileName - The name of the file
 * @returns {string} The file type
 */
function getFileType(fileName) {
  const extension = fileName.split('.').pop().toLowerCase();
  
  const typeMap = {
    'gb': 'GenBank',
    'genbank': 'GenBank',
    'fasta': 'FASTA',
    'fa': 'FASTA',
    'fastq': 'FASTQ',
  };
  
  return typeMap[extension] || 'Unknown';
}

/**
 * Computes MD5 hash of a file using crypto-js
 * @param {File} file - The file to hash
 * @returns {Promise<string>} Hex string of the MD5 hash
 */
async function computeMD5(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    
    reader.onload = (e) => {
      try {
        const wordArray = CryptoJS.lib.WordArray.create(e.target.result);
        const hash = CryptoJS.MD5(wordArray).toString();
        resolve(hash);
      } catch (error) {
        reject(error);
      }
    };
    
    reader.onerror = () => {
      reject(new Error('Failed to read file for hashing'));
    };
    
    reader.readAsArrayBuffer(file);
  });
}

/**
 * Converts TSV (Tab-Separated Values) string to JSON array
 * @param {string} tsvString - The TSV content as a string
 * @param {Object} options - Configuration options
 * @param {boolean} options.trimValues - Whether to trim whitespace from values (default: true)
 * @param {boolean} options.skipEmptyRows - Whether to skip empty rows (default: true)
 * @returns {Array<Object>} Array of objects representing the TSV data
 */
function tsvToJson(tsvString, options = {}) {
  const { trimValues = true } = options;
  
  // Helper function to remove surrounding quotes from a value
  const removeQuotes = (value) => {
    value = trimValues ? value.trim() : value;
    // Remove surrounding quotes if present
    if ((value.startsWith('"') && value.endsWith('"')) || 
        (value.startsWith("'") && value.endsWith("'"))) {
      return value.slice(1, -1);
    }
    return value;
  };
  
  // Split into lines
  const lines = tsvString.split(/\r?\n/).filter(line => line.trim());
  
  if (lines.length < 2) {
    return {};
  }
  
  // Extract headers from first line
  const headers = lines[0].split('\t').map(h => removeQuotes(h));
  
  // Extract first data row
  const values = lines[1].split('\t').map(v => removeQuotes(v));
  
  // Create object from headers and values
  const result = {};
  headers.forEach((header, index) => {
    result[header] = values[index] || '';
  });
  
  return result;
}

/**
 * Converts TSV file to JSON (for use with File API)
 * @param {File} file - The TSV file object
 * @param {Object} options - Configuration options
 * @returns {Promise<Array<Object>>} Promise that resolves to JSON array
 */
async function tsvFileToJson(file, options = {}) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    
    reader.onload = (e) => {
      try {
        const json = tsvToJson(e.target.result, options);
        resolve(json);
      } catch (error) {
        reject(error);
      }
    };
    
    reader.onerror = () => {
      reject(new Error('Failed to read file'));
    };
    
    reader.readAsText(file);
  });
}

/**
 * Enhances JSON with study metadata and file information
 * @param {Object} jsonData - The base JSON object from TSV
 * @param {string} studyId - The study identifier
 * @param {string} analysisName - The name of the analysis type
 * @param {string} analysisVersion - The version of the analysis
 * @param {Array<File>} files - Array of File objects to process
 * @param {Object} options - Additional options
 * @param {string} options.fileAccess - File access level (default: "open")
 * @param {string} options.dataType - Data type description (default: "Cholera Genome")
 * @returns {Promise<Object>} Enhanced JSON object with metadata
 */
async function enhanceWithMetadata(jsonData, studyId, analysisName, analysisVersion, files, options = {}) {
  const { fileAccess = "open", dataType = "Cholera Genome" } = options;
  
  // Create analysis type object
  const analysisType = {
    name: analysisName,
    version: Number(analysisVersion)
  };
  
  // Process all files to get their metadata
  const filePromises = files.map(async (file) => {
    const md5sum = await computeMD5(file);
    
    return {
      fileName: file.name,
      fileType: getFileType(file.name),
      fileSize: file.size,
      fileMd5sum: md5sum,
      fileAccess: fileAccess,
      dataType: dataType
    };
  });
  
  const filesMetadata = await Promise.all(filePromises);
  
  // Construct the enhanced object
  return {
    studyId: studyId,
    analysisType: analysisType,
    files: filesMetadata,
    ...jsonData  // Spread the original JSON data
  };
}

// Track uploaded files
let tsvFile = null;
let genomeFiles = [];

// Check if all required inputs are ready
function checkIfReady() {
  const processBtn = document.getElementById("processBtn");
  const studyIdInput = document.getElementById("studyId");
  const analysisNameInput = document.getElementById("analysisName");
  const analysisVersionInput = document.getElementById("analysisVersion");
  
  // Check if all elements exist first
  if (!processBtn || !studyIdInput || !analysisNameInput || !analysisVersionInput) {
    console.log("Some elements not found");
    return;
  }
  
  const studyId = studyIdInput.value.trim();
  const analysisName = analysisNameInput.value.trim();
  const analysisVersion = analysisVersionInput.value.trim();
  
  console.log("Checking ready state:", {
    tsvFile: !!tsvFile,
    genomeFilesCount: genomeFiles.length,
    studyId,
    analysisName,
    analysisVersion
  });
  
  if (tsvFile && genomeFiles.length > 0 && studyId && analysisName && analysisVersion) {
    processBtn.disabled = false;
    console.log("Button enabled!");
  } else {
    processBtn.disabled = true;
    console.log("Button disabled - missing requirements");
  }
}

// Wait for DOM to be fully loaded
document.addEventListener("DOMContentLoaded", () => {
  // Handle TSV file input
  document.getElementById("tsvInput").addEventListener("change", (event) => {
    tsvFile = event.target.files[0];
    console.log("TSV file selected:", tsvFile?.name);
    checkIfReady();
  });

  // Handle genome files input
  document.getElementById("genomeFiles").addEventListener("change", (event) => {
    genomeFiles = Array.from(event.target.files);
    console.log("Genome files selected:", genomeFiles.length);
    checkIfReady();
  });

  // Add event listeners to text inputs
  document.getElementById("studyId").addEventListener("input", checkIfReady);
  document.getElementById("analysisName").addEventListener("input", checkIfReady);
  document.getElementById("analysisVersion").addEventListener("input", checkIfReady);
  
  console.log("Event listeners attached");
});

// Handle process button click
document.getElementById("processBtn").addEventListener("click", async () => {
  const output = document.getElementById("output");
  const processBtn = document.getElementById("processBtn");
  
  try {
    processBtn.disabled = true;
    output.textContent = "Processing files... This may take a moment for large files.";
    
    // Get form values
    const studyId = document.getElementById("studyId").value.trim();
    const analysisName = document.getElementById("analysisName").value.trim();
    const analysisVersion = document.getElementById("analysisVersion").value.trim();
    
    // Parse TSV file
    const tsvData = await tsvFileToJson(tsvFile);
    
    // Enhance with metadata from genome files
    const result = await enhanceWithMetadata(
      tsvData,
      studyId,
      analysisName,
      analysisVersion,
      genomeFiles
    );
    
    output.textContent = JSON.stringify(result, null, 2);
    
  } catch (err) {
    output.textContent = "Error: " + err.message;
    console.error(err);
  } finally {
    processBtn.disabled = false;
    checkIfReady();
  }
});