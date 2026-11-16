# EBMS OS engine build.  Works with GNU make + gcc on Linux, macOS, and MinGW on Windows.
#   make            -> bin/ebms_os (bin/ebms_os.exe on Windows)
#   make test       -> builds and runs every C test in tests/
#   make test-asan  -> same tests with AddressSanitizer + UndefinedBehaviorSanitizer (Linux/macOS gcc)
#   make test-tsan  -> same tests with ThreadSanitizer (Linux/macOS gcc)
CC ?= gcc
CSTD = -std=c11
WARN = -Wall -Wextra
INC = -Iinclude
ifeq ($(OS),Windows_NT)
  EXE = .exe
  THREADLIBS =
else
  EXE =
  THREADLIBS = -pthread
endif

MODULE_SRC = src/common.c src/json_parser.c src/scheduler.c src/banker.c src/deadlock.c \
             src/producer_consumer.c src/readers_writers.c
MAIN = bin/ebms_os$(EXE)
TESTS = test_json test_scheduler test_banker test_deadlock test_producer_consumer test_readers_writers

.PHONY: all test test-asan test-tsan clean

all: $(MAIN)

bin:
	mkdir -p bin

$(MAIN): src/main.c $(MODULE_SRC) | bin
	$(CC) $(CSTD) $(WARN) -O2 $(INC) -o $@ src/main.c $(MODULE_SRC) $(THREADLIBS)

bin/%$(EXE): tests/%.c $(MODULE_SRC) | bin
	$(CC) $(CSTD) $(WARN) -g $(INC) $(EXTRA) -o $@ $< $(MODULE_SRC) $(THREADLIBS)

test: $(MAIN) $(addprefix bin/,$(addsuffix $(EXE),$(TESTS)))
	@for t in $(TESTS); do echo "== $$t"; ./bin/$$t$(EXE) || exit 1; done

test-asan: EXTRA = -fsanitize=address,undefined -fno-omit-frame-pointer
test-asan: clean-tests $(addprefix bin/,$(addsuffix $(EXE),$(TESTS)))
	@for t in $(TESTS); do echo "== $$t (asan+ubsan)"; ./bin/$$t$(EXE) || exit 1; done

test-tsan: EXTRA = -fsanitize=thread
test-tsan: clean-tests $(addprefix bin/,$(addsuffix $(EXE),$(TESTS)))
	@for t in $(TESTS); do echo "== $$t (tsan)"; ./bin/$$t$(EXE) || exit 1; done

clean-tests:
	rm -f $(addprefix bin/,$(addsuffix $(EXE),$(TESTS)))

clean:
	rm -rf bin
