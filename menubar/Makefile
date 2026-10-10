PROJECT := WiibalanceToolbar/WiibalanceToolbar.xcodeproj
BUILD := $(CURDIR)/build
XCODEBUILD := xcodebuild -project $(PROJECT) -scheme WiibalanceToolbar -derivedDataPath "$(BUILD)" -destination 'platform=macOS,arch=$(shell uname -m)'

.PHONY: build run test

build:
	$(XCODEBUILD) -configuration Release -quiet build

run: build
	open "$(BUILD)/Build/Products/Release/WiibalanceToolbar.app"

test:
	$(XCODEBUILD) -configuration Debug -quiet test
