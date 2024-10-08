from __future__ import print_function
import sys

sys.path.append("include")
from builtins import int
import sys
import GageSupport as gs
import GageConstants as gc
import numpy as np
import PyGage3_64 as PyGage


def convert_adc_to_volts(x, stHeader, scale_factor, offset):
    return (
        ((stHeader["SampleOffset"] - x) / stHeader["SampleRes"]) * scale_factor
    ) + offset


def normalize(vec):
    return vec / np.max(abs(vec))


def configure_system(handle, filename, segment_size=None):
    acq, sts = gs.LoadAcquisitionConfiguration(handle, filename)

    # added this
    if segment_size is not None:
        if isinstance(acq, dict):
            acq["Depth"] = segment_size
            acq["SegmentSize"] = segment_size

    if isinstance(acq, dict) and acq:
        status = PyGage.SetAcquisitionConfig(handle, acq)
        if status < 0:
            return status
    else:
        print("Using defaults for acquisition parameters")

    if sts == gs.INI_FILE_MISSING:
        print("Missing ini file, using defaults")
    elif sts == gs.PARAMETERS_MISSING:
        print(
            "One or more acquisition parameters missing, using defaults for missing values"
        )

    system_info = PyGage.GetSystemInfo(handle)
    acq = PyGage.GetAcquisitionConfig(
        handle
    )  # check for error - copy to GageAcquire.py

    channel_increment = gs.CalculateChannelIndexIncrement(
        acq["Mode"], system_info["ChannelCount"], system_info["BoardCount"]
    )

    missing_parameters = False
    for i in range(1, system_info["ChannelCount"] + 1, channel_increment):
        chan, sts = gs.LoadChannelConfiguration(handle, i, filename)
        if isinstance(chan, dict) and chan:
            status = PyGage.SetChannelConfig(handle, i, chan)
            if status < 0:
                return status
        else:
            print("Using default parameters for channel ", i)

        if sts == gs.PARAMETERS_MISSING:
            missing_parameters = True

    if missing_parameters:
        print(
            "One or more channel parameters missing, using defaults for missing values"
        )

    missing_parameters = False
    # in this example we're only using 1 trigger source, if we use
    # system_info['TriggerMachineCount'] we'll get warnings about
    # using default values for the trigger engines that aren't in
    # the ini file
    trigger_count = 1
    for i in range(1, trigger_count + 1):
        trig, sts = gs.LoadTriggerConfiguration(handle, i, filename)
        if isinstance(trig, dict) and trig:
            status = PyGage.SetTriggerConfig(handle, i, trig)
            if status < 0:
                return status
        else:
            print("Using default parameters for trigger ", i)

        if sts == gs.PARAMETERS_MISSING:
            missing_parameters = True

    if missing_parameters:
        print(
            "One or more trigger parameters missing, using defaults for missing values"
        )

    status = PyGage.Commit(handle)
    return status, channel_increment


def get_handle():
    status = PyGage.Initialize()
    if status < 0:
        return status
    else:
        handle = PyGage.GetSystem(0, 0, 0, 0)
        return handle


def get_data(handle, mode, app, system_info, channel_increment):
    status = PyGage.StartCapture(handle)
    if status < 0:
        return status

    status = PyGage.GetStatus(handle)
    while status != gc.ACQ_STATUS_READY:
        status = PyGage.GetStatus(handle)

    acq = PyGage.GetAcquisitionConfig(handle)

    # Validate the start address and the length. This is especially
    # necessary if trigger delay is being used.
    min_start_address = acq["TriggerDelay"] + acq["Depth"] - acq["SegmentSize"]
    if app["StartPosition"] < min_start_address:
        print(
            "\nInvalid Start Address was changed from {0} to {1}".format(
                app["StartPosition"], min_start_address
            )
        )
        app["StartPosition"] = min_start_address

    max_length = acq["TriggerDelay"] + acq["Depth"] - min_start_address
    if app["TransferLength"] > max_length:
        print(
            "\nInvalid Transfer Length was changed from {0} to {1}".format(
                app["TransferLength"], max_length
            )
        )
        app["TransferLength"] = max_length

    stHeader = {}
    if acq["ExternalClock"]:
        stHeader["SampleRate"] = acq["SampleRate"] / acq["ExtClockSampleSkip"] * 1000
    else:
        stHeader["SampleRate"] = acq["SampleRate"]

    stHeader["Start"] = app["StartPosition"]
    stHeader["Length"] = app["TransferLength"]
    stHeader["SampleSize"] = acq["SampleSize"]
    stHeader["SampleOffset"] = acq["SampleOffset"]
    stHeader["SampleRes"] = acq["SampleResolution"]
    stHeader["SegmentNumber"] = 1  # this example only does single capture
    stHeader["SampleBits"] = acq["SampleBits"]

    if app["SaveFileFormat"] == gs.TYPE_SIG:
        stHeader["SegmentCount"] = 1
    else:
        stHeader["SegmentCount"] = acq["SegmentCount"]

    buffer_list = []
    for i in range(1, system_info["ChannelCount"] + 1, channel_increment):
        buffer = PyGage.TransferData(
            handle, i, 0, 1, app["StartPosition"], app["TransferLength"]
        )
        if isinstance(buffer, int):  # an error occurred
            print("Error transferring channel ", 1)
            return buffer
        buffer_list.append(buffer)

    # if call succeeded (buffer is not an integer) then
    # buffer[0] holds the actual data, buffer[1] holds
    # the actual start and buffer[2] holds the actual length

    chan = PyGage.GetChannelConfig(handle, 1)
    stHeader["InputRange"] = chan["InputRange"]
    stHeader["DcOffset"] = chan["DcOffset"]

    scale_factor = stHeader["InputRange"] / 2000
    offset = stHeader["DcOffset"] / 1000

    # buffer[0] is a numpy array I don't know why in their code they converted
    # the array to list and then used map, it's a heck of a lot longer to do
    # it that way.
    data_list = []
    for buffer in buffer_list:
        data = convert_adc_to_volts(buffer[0], stHeader, scale_factor, offset)
        data_list.append(data)

    return status, data_list


def acquire(segment_size, handle=None, inifile="../GaGe_Python/Acquire.ini"):
    try:
        # initialization common amongst all sample programs:
        # ---------------------------------------------------------------------
        # if handle is None, then get the handle for the first card available
        if handle is None:
            handle = get_handle()
            if handle < 0:
                # get error string
                error_string = PyGage.GetErrorString(handle)
                print("Error: ", error_string)
                raise SystemExit

        # in case handle was supplied, make sure handle is an int here if it
        # was supplied and doesn't refer to a card, the error will be caught
        # later
        assert isinstance(handle, int)

        system_info = PyGage.GetSystemInfo(handle)
        if not isinstance(
            system_info, dict
        ):  # if it's not a dict, it's an int indicating an error
            print("Error: ", PyGage.GetErrorString(system_info))
            PyGage.FreeSystem(handle)
            raise SystemExit

        print("\nBoard Name: ", system_info["BoardName"])

        status, channel_increment = configure_system(handle, inifile, segment_size)
        if status < 0:
            # get error string
            error_string = PyGage.GetErrorString(status)
            print("Error: ", error_string)
        else:
            acq_config = PyGage.GetAcquisitionConfig(handle)
            app, sts = gs.LoadApplicationConfiguration(inifile)

            if segment_size is not None:
                app["TransferLength"] = segment_size

            # we don't need to check for gs.INI_FILE_MISSING because if there's no ini file
            # we've already reported when calling configure_system
            if sts == gs.PARAMETERS_MISSING:
                print(
                    "One or more application parameters missing, using defaults for missing values"
                )

            # -----------------------------------------------------------------
            # initialization done

            status, data_list = get_data(
                handle, acq_config["Mode"], app, system_info, channel_increment
            )
            if isinstance(status, int):
                if status < 0:
                    error_string = PyGage.GetErrorString(status)
                    print("Error: ", error_string)

                # these error checks regard the saving of the data

            # free the handle and return the data data
            PyGage.FreeSystem(handle)
            return data_list
    except KeyboardInterrupt:
        print("Exiting program")

    PyGage.FreeSystem(handle)
