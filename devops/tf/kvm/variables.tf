variable "cloud_name" {
  description = "Name of the cloud in clouds.yaml"
  type        = string
  default     = "KVM@TACC"
}

variable "suffix" {
  description = "Project ID suffix for Chameleon naming convention (e.g. proj99)"
  type        = string
}

variable "key_pair" {
  description = "Name of the SSH key pair registered on KVM@TACC"
  type        = string
}

variable "reservation_id" {
  description = "Chameleon reservation/lease ID (if applicable)"
  type        = string
  default     = ""
}

variable "image" {
  description = "Chameleon VM image name"
  type        = string
  default     = "CC-Ubuntu24.04"
}

variable "flavor" {
  description = "Instance flavor for all nodes"
  type        = string
  default     = "m1.large"
}

variable "node_count" {
  description = "Number of K8S nodes"
  type        = number
  default     = 3
}

variable "private_subnet_cidr" {
  description = "CIDR for the private cluster network"
  type        = string
  default     = "192.168.1.0/24"
}

variable "private_ips" {
  description = "Fixed private IPs for each node"
  type        = list(string)
  default     = ["192.168.1.11", "192.168.1.12", "192.168.1.13"]
}
