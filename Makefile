TARGET=~/Library/Application\ Support/cura/5.0/plugins/Snapmaker2Plugin/Snapmaker2Plugin/

PROJ = Snapmaker2Plugin

SRC = plugin.json \
	__init__.py \
	SM2GCodeWriter.py \
	SM2OutputDeviceManager.py

REL = $(SRC) \
	LICENSE \
	README.md \
	README.en-us.md

install:
	cp -f $(SRC) $(TARGET)

release:
	mkdir $(PROJ)
	cp $(REL) $(PROJ)
	zip -r $(PROJ).zip $(PROJ)
	rm -fr $(PROJ)
