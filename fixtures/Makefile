.PHONY: all clean packages repos images iso squashfs

all: packages repos images

packages:
	./create-package.sh --build -v 1.0-1 -o packages/ hello
	./create-package.sh --build -v 2.0-1 -a amd64 -o packages/ libfoo
	./create-package.sh --build -v 1.0-1 -d "hello" -o packages/ depends-on-hello

repos: packages
	./create-repo.sh -a amd64,arm64 -p packages/hello_1.0-1_all.deb \
	    -p packages/libfoo_2.0-1_amd64.deb -o repositories/ -m test-repo

clean:
	find packages/ -type f ! -name '.gitkeep' -delete
	rm -rf packages/*/
	find repositories/ -type f ! -name '.gitkeep' -delete
	rm -rf repositories/*/
	rm -f images/test.iso
	rm -f images/test.squashfs

images: iso squashfs

iso: images/test.iso

images/test.iso:
	./build-iso.sh

squashfs: images/test.squashfs

images/test.squashfs:
	./build-squashfs.sh
