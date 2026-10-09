/ mock_trades_backfill1's target: one synthetic FX trade.
mock_trades:([]time:`timestamp$(); sym:`g#`symbol$(); side:`symbol$(); qty:`float$(); px:`float$())

/ What mock_positions computes from each published mock_trades window: one pair's net position.
mock_positions:([]time:`timestamp$(); sym:`g#`symbol$(); window:`timestamp$(); net_qty:`float$(); vwap:`float$(); trades:`long$())

/ mock_ticks1's output: one synthetic top-of-book quote.
mock_ticks:([]time:`timestamp$(); source_time:`timestamp$(); sym:`g#`symbol$(); bid:`float$(); ask:`float$())

/ mock_mids1's output: the mid and spread of each mock_ticks quote.
mock_mids:([]time:`timestamp$(); source_time:`timestamp$(); sym:`g#`symbol$(); mid:`float$(); spread:`float$())
