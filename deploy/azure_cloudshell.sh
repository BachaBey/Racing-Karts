#!/usr/bin/env bash
# One-shot Azure deployment, meant to be run in Azure Cloud Shell (Bash):
#
#   curl -sL https://raw.githubusercontent.com/BachaBey/Racing-Karts/main/deploy/azure_cloudshell.sh | bash
#
# * finds a region allowed by your subscription's policy (student subscriptions are
#   limited to a few regions) where a cheap VM size is actually available,
# * creates an Ubuntu VM there, opens TCP 5555 + UDP 5556,
# * cloud-init clones this repo on the VM and installs the server as a systemd service.
# No key download and nothing to install on your PC.
set -u

REPO=https://github.com/BachaBey/Racing-Karts
VM=kart-server
# cheapest first; *p* sizes are Arm64 (the server is pure Python, so that works too)
SIZES="Standard_B1s Standard_B1ls Standard_B1ms Standard_B2ats_v2 Standard_B2pts_v2 Standard_B2als_v2 Standard_B2s \
Standard_B2s_v2 Standard_B2as_v2 Standard_B2ps_v2 Standard_B2pls_v2 Standard_A1_v2 Standard_A2_v2 \
Standard_D2as_v5 Standard_D2s_v5 Standard_D2ps_v5 Standard_D2pls_v5 Standard_D2as_v6 Standard_D2s_v6 \
Standard_D2ads_v5 Standard_D2s_v4 Standard_D2as_v4 Standard_D2s_v3 Standard_F2s_v2 Standard_DC1s_v2"

echo "== subscription: $(az account show --query name -o tsv)"
REGIONS=$(az policy assignment list --query "[].parameters.listOfAllowedLocations.value[]" -o tsv 2>/dev/null | sort -u | tr '\n' ' ')
if [ -z "$REGIONS" ]; then
  REGIONS="italynorth spaincentral germanywestcentral northeurope westeurope swedencentral polandcentral"
fi
echo "== regions to try: $REGIONS"

cat > /tmp/kart-cloud-init.yml <<EOF
#cloud-config
package_update: true
packages: [git, python3]
runcmd:
  - git clone --depth 1 $REPO /opt/kart-src
  - bash /opt/kart-src/deploy/setup_server.sh 5555 5556
EOF

for R in $REGIONS; do
  # A size is unusable only if it is restricted for the whole REGION ("Location").
  # Restrictions of type "Zone" only block some availability zones; we don't use zones.
  AVAIL=$(az vm list-skus -l "$R" --resource-type virtualMachines \
          --query "[?length(restrictions[?type=='Location'])==\`0\`].name" -o tsv)
  SMALL=$(echo "$AVAIL" | grep -E '^Standard_(B1|B2|A1_v2|A2_v2|D2|F2s|DC1)' | tr '\n' ' ')
  echo ""
  echo "== $R: $(echo "$AVAIL" | grep -c . ) sizes usable; small ones: ${SMALL:-none}"
  CANDIDATES=""
  for S in $SIZES; do echo "$AVAIL" | grep -qx "$S" && CANDIDATES="$CANDIDATES $S"; done
  # fallback: any other small size this region offers
  for S in $SMALL; do case " $CANDIDATES " in *" $S "*) ;; *) CANDIDATES="$CANDIDATES $S" ;; esac; done
  for S in $CANDIDATES; do
    echo "$AVAIL" | grep -qx "$S" || continue
    IMG=Canonical:ubuntu-24_04-lts:server:latest
    case "$S" in *B2p*|*D2p*) IMG=Canonical:ubuntu-24_04-lts:server-arm64:latest ;; esac
    RG="kart-rg-$R-$(echo "${S#Standard_}" | tr 'A-Z' 'a-z')"   # unique per attempt (failed ones are deleted async)
    echo ""
    echo ">> trying $S in $R ..."
    az group create -n "$RG" -l "$R" -o none || continue
    if az vm create -g "$RG" -n "$VM" -l "$R" --size "$S" --image "$IMG" \
         --admin-username azureuser --generate-ssh-keys --public-ip-sku Standard \
         --custom-data /tmp/kart-cloud-init.yml -o none; then
      az vm open-port -g "$RG" -n "$VM" --port 5555-5556 --priority 1010 -o none
      IP=$(az vm show -d -g "$RG" -n "$VM" --query publicIps -o tsv)
      echo ""
      echo "=============================================================="
      echo " SUCCESS: $S in $R"
      echo " Resource group : $RG"
      echo " Public IP      : $IP"
      echo " Ports opened   : TCP+UDP 5555-5556"
      echo " The game server installs itself in ~2-3 minutes (cloud-init)."
      echo " Players:  TurboKartArena.exe  -> server address  $IP"
      echo " Stop VM when idle:  az vm deallocate -g $RG -n $VM"
      echo " Start it again:     az vm start -g $RG -n $VM"
      echo "=============================================================="
      exit 0
    fi
    echo "   failed, cleaning up and trying the next option"
    az group delete -n "$RG" --yes --no-wait
  done
done
echo ""
echo "No region/size combination worked. Copy the errors above and ask for help."
exit 1
