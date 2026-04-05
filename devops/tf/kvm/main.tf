# SmartQueue Infrastructure - Chameleon KVM@TACC
# Authentication via clouds.yaml (application credentials)
# See: terraform.tfvars.example for required variables

provider "openstack" {
  cloud = var.cloud_name
}
