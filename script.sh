#!/bin/bash
#SBATCH --job-name=test       # Job name
#SBATCH --output=output.txt           # Standard output file
#SBATCH --error=error.txt             # Standard error file
#SBATCH --partition=small   # Partition or queue name
#SBATCH --nodes=1                     # Number of nodes
#SBATCH --ntasks-per-node=1           # Number of tasks per node
#SBATCH --cpus-per-task=2            # Number of CPU cores per task
#
#SBATCH --gres=gpu:0 ##Define number of GPUs
#SBATCH --mail-type=END               # Send email at job completion
#SBATCH --mail-user=fursule.1@iitj.ac.in   # Email address for notifications

# Load necessary modules (if needed)
module load python/3.10.pytorch

# Replace X with mkrum, foolsgold, or bulyan
PARAM="mkrum"

# Run the Python script with the specified parameter file
python3 main.py --params utils/${PARAM}.yaml

# Optionally, you can include cleanup commands here (e.g., after the job finishes)
# For example:
# rm some_temp_file.txt
