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
