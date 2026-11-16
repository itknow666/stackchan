cmake_minimum_required(VERSION 3.16)

project(stackchan_firmware_tests LANGUAGES CXX)

enable_testing()

add_executable(motion_math_test
    motion_math_test.cpp
    ../main/stackchan/motion/motion_math.cpp
)

target_include_directories(motion_math_test PRIVATE
    ../main
)

target_compile_features(motion_math_test PRIVATE cxx_std_17)

add_test(NAME motion_math_test COMMAND $<TARGET_FILE:motion_math_test>)

add_executable(head_pet_voice_policy_test head_pet_voice_policy_test.cpp)
target_compile_features(head_pet_voice_policy_test PRIVATE cxx_std_17)
add_test(NAME head_pet_voice_policy_test COMMAND $<TARGET_FILE:head_pet_voice_policy_test>)

add_executable(agent_ble_queue_test agent_ble_queue_test.cpp)
target_compile_features(agent_ble_queue_test PRIVATE cxx_std_17)
add_test(NAME agent_ble_queue_test COMMAND $<TARGET_FILE:agent_ble_queue_test>)

add_executable(network_audio_prebuffer_test network_audio_prebuffer_test.cpp)
target_compile_features(network_audio_prebuffer_test PRIVATE cxx_std_17)
add_test(NAME network_audio_prebuffer_test COMMAND $<TARGET_FILE:network_audio_prebuffer_test>)

# ArduinoJson is installed by the normal firmware dependency setup; no ESP SDK
# is needed to exercise the exact manifest parser on a host.
find_path(ARDUINOJSON_INCLUDE_DIR ArduinoJson.hpp
    HINTS "${CMAKE_CURRENT_SOURCE_DIR}/../components/ArduinoJson/src")
if(NOT ARDUINOJSON_INCLUDE_DIR)
    message(FATAL_ERROR "Set ARDUINOJSON_INCLUDE_DIR to ArduinoJson/src (firmware dependency)")
endif()
add_executable(sd_voice_manifest_test sd_voice_manifest_test.cpp
    ../main/hal/board/sd_voice_manifest.cc)
target_include_directories(sd_voice_manifest_test PRIVATE ${ARDUINOJSON_INCLUDE_DIR})
target_compile_features(sd_voice_manifest_test PRIVATE cxx_std_17)
add_test(NAME sd_voice_manifest_test COMMAND $<TARGET_FILE:sd_voice_manifest_test>)
add_executable(local_playback_lease_test local_playback_lease_test.cpp)
target_compile_features(local_playback_lease_test PRIVATE cxx_std_17)
add_test(NAME local_playback_lease_test COMMAND $<TARGET_FILE:local_playback_lease_test>)
add_executable(sd_voice_file_test sd_voice_file_test.cpp
    ../main/hal/board/sd_voice_file.cc ../main/hal/board/sd_voice_manifest.cc)
target_include_directories(sd_voice_file_test PRIVATE ${ARDUINOJSON_INCLUDE_DIR})
target_compile_features(sd_voice_file_test PRIVATE cxx_std_17)
add_test(NAME sd_voice_file_test COMMAND $<TARGET_FILE:sd_voice_file_test>)

set(STACKCHAN_AUDIO_SERVICE_SOURCE
    "${CMAKE_CURRENT_SOURCE_DIR}/../xiaozhi-esp32/main/audio/audio_service.cc"
    CACHE FILEPATH "Patched audio service source for the SDK-free decoder lock test")
if(EXISTS "${STACKCHAN_AUDIO_SERVICE_SOURCE}")
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
    add_test(NAME decoder_configuration_lock_test
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/check_decoder_lock.py
        ${STACKCHAN_AUDIO_SERVICE_SOURCE} --compiler ${CMAKE_CXX_COMPILER})
endif()
