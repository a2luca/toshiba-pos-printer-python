{
  description = "Toshiba TCx 6145-1TN USB receipt printer driver";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      packages = forAllSystems (system:
        let pkgs = nixpkgs.legacyPackages.${system}; in
        {
          default = pkgs.python3Packages.buildPythonPackage {
            pname = "toshiba-6145-driver";
            version = "1.0.0";
            src = ./.;
            format = "other";

            propagatedBuildInputs = with pkgs.python3Packages; [
              pyusb
              pillow
              qrcode
            ];

            installPhase = ''
              install -Dm644 toshiba_6145_driver.py \
                $out/lib/python3/dist-packages/toshiba_6145_driver.py
              install -Dm755 examples/demo.py \
                $out/bin/toshiba-6145-demo
            '';

            meta = {
              description = "Pure Python USB driver for the Toshiba TCx 6145-1TN receipt printer";
              license = pkgs.lib.licenses.mit;
              platforms = pkgs.lib.platforms.linux;
            };
          };
        }
      );

      devShells = forAllSystems (system:
        let pkgs = nixpkgs.legacyPackages.${system}; in
        {
          default = pkgs.mkShell {
            packages = with pkgs; [
              (python3.withPackages (ps: with ps; [
                pyusb
                pillow
                qrcode
              ]))
              usbutils   # lsusb
            ];

            shellHook = ''
              echo "Toshiba 6145 dev shell"
              echo "  python3 toshiba_6145_driver.py   — self-test"
              echo "  python3 examples/demo.py          — full feature demo"
              echo ""
              echo "Note: USB access requires root or a udev rule:"
              echo '  SUBSYSTEM=="usb", ATTRS{idVendor}=="0f66", ATTRS{idProduct}=="4535", MODE="0666"'
            '';
          };
        }
      );
    };
}
