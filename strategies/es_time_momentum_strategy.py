from typing import List, Dict
from datetime import datetime
from datetime import timedelta

from vnpy.trader.utility import ArrayManager, Interval
from vnpy.trader.object import TickData, BarData
from vnpy_portfoliostrategy import StrategyTemplate, StrategyEngine
from vnpy_portfoliostrategy.utility import PortfolioBarGenerator
from vnpy.trader.constant import Interval, Direction, Offset, Status

import numpy as np
import pandas as pd

class ESTimeMomentumStrategy(StrategyTemplate):
    """"""

    author = "V"

    open_time = "10:30:00"
    close_time = "22:30:00"
    window = 23
    thre = 1.2
    fixed_size = 5
    algo_limit_place = 0.0
    algo_limit_spread = 1.0

    parameters = [
        "open_time",
        "close_time",
        "window",
        "thre",
        "fixed_size",
        "algo_limit_place",
        "algo_limit_spread"
    ]

    current_pos = 0
    trading_in_process = False
    vt_orderids = -1
    last_price = -1
    z_score = 0
    target_today = 0
    target_middle = 0
    snap_price = -1

    variables = [
        "current_pos",
        "trading_in_process",
        "vt_orderids",
        "last_price",
        "z_score",
        "target_today",
        "target_middle",
        "snap_price"
    ]

    def __init__(self, strategy_engine: StrategyEngine, strategy_name: str, vt_symbols: List[str], setting: dict):
        """"""
        super().__init__(strategy_engine, strategy_name, vt_symbols, setting)

        self.last_tick_time: datetime = None
        self.targets: Dict[str, int] = {}

        self.t_open_time = datetime.strptime(self.open_time, '%H:%M:%S').time()
        self.t_close_time = datetime.strptime(self.close_time, '%H:%M:%S').time()
        self.bar_open_time = datetime.strptime(self.open_time, '%H:%M:%S') - timedelta(minutes=30)
        self.bar_close_time = datetime.strptime(self.close_time, '%H:%M:%S') - timedelta(minutes=30)

        self.current_pos = 0
        self.trading_in_process = False
        self.vt_orderids = -1
        self.vt_orderids_datetime = -1
        self.last_price = -1
        self.z_score = 0
        self.target_today = 0
        self.target_middle = 0
        self.snap_price = -1

        self.z_score_latest = 0
        self.target_today_latest = 0
        self.target_middle_latest = 0
        self.snap_price_latest = -1

        self.open_orderids = []
        self.fake_orderids = []
        self.prev_traded = 0
        self.target = 0
        self.vt_symbol = vt_symbols[0]
        self.chase_interval = 20  # 拆单间隔:秒
        self.prev_debug_message = ""
        self.debug_message = ""

        #For order handling
        self.chase_long_trigger = False
        self.chase_short_trigger = False
        self.last_vt_orderid = ""
        self.long_trade_volume = 0
        self.short_trade_volume = 0
        self.cancel_status = False

        # Obtain contract info
        self.ams: Dict[str, ArrayManager] = {}
        for vt_symbol in self.vt_symbols:
            self.ams[vt_symbol] = ArrayManager()
            self.targets[vt_symbol] = 0

        self.pbg = PortfolioBarGenerator(self.on_bars, 30, self.on_30min_bar, Interval.MINUTE)
        self.put_event()

    def on_init(self):
        """
        Callback when strategy is inited.
        """
        self.write_log("策略初始化")

        self.load_bars(self.window + 1)

    def on_start(self):
        """
        Callback when strategy is started.
        """
        self.write_log("策略启动")
        self.t_open_time = datetime.strptime(self.open_time, '%H:%M:%S').time()
        self.t_close_time = datetime.strptime(self.close_time, '%H:%M:%S').time()
        self.bar_open_time = datetime.strptime(self.open_time, '%H:%M:%S') - timedelta(minutes=30)
        self.bar_close_time = datetime.strptime(self.close_time, '%H:%M:%S') - timedelta(minutes=30)

        # self.current_pos = 0
        # self.trading_in_process = False
        # self.vt_orderids = -1
        # self.vt_orderids_datetime = -1
        # self.last_price = -1
        # self.z_score = 0

        # self.open_orderids = []
        # self.fake_orderids = []
        # self.prev_traded = 0
        # self.target = 0
        # self.vt_symbol = vt_symbols[0]
        # self.chase_interval = 20  # 拆单间隔:秒
        # self.prev_debug_message = ""
        # self.debug_message = ""

        #For order handling
        # self.chase_long_trigger = False
        # self.chase_short_trigger = False
        # self.last_vt_orderid = ""
        # self.long_trade_volume = 0
        # self.short_trade_volume = 0
        # self.cancel_status = False

        # Prevent got replaced by the data from json file
        self.z_score = self.z_score_latest
        self.target_today = self.target_today_latest
        self.target_middle = self.target_middle_latest
        self.snap_price = self.snap_price_latest

        # Get saved pos for further action (e.g. close all after trading period)
        self.current_pos = self.get_pos(self.vt_symbol)
        self.write_log(f"outstanding pos : {self.current_pos}")
        current_time = datetime.now().time()

        #Close all pos outside trading period
        if self.current_pos != 0:
            close_position_period = self.time_in_close_position_period(self.t_open_time, self.t_close_time, current_time)
            if close_position_period:
                if self.current_pos > 0:
                    self.target = -self.current_pos
                    self.write_log(f"Close all outstanding pos outside trading period. Begin SHORT {abs(self.current_pos)} pos.")
                elif self.current_pos < 0:
                    self.target = -self.current_pos
                    self.write_log(f"Close all outstanding pos outside trading period. Begin LONG {abs(self.current_pos)} pos.")

        # Check trading period and do the pos offset
        in_trading_period = self.time_in_trading_period(self.t_open_time, self.t_close_time, current_time)
        if in_trading_period and self.current_pos == 0:
            tick = self.get_tick(self.vt_symbol)
            # self.write_log(tick)
            calculated_pos = self.target
            self.write_log(f"calculated pos : {calculated_pos}")
            pos_offset = calculated_pos - self.current_pos

            if pos_offset != 0:
                if pos_offset > 0:
                    if tick.last_price < self.snap_price:
                        self.write_log(f"Current price < snap price. Doing position offset(trades {pos_offset}). Total Pos : {self.current_pos}")
                        self.target = pos_offset
                    else:
                        self.write_log(f"Current price > snap price, not favor for LONG. Offset(trades {pos_offset}) skipped.")
                        self.target_middle = pos_offset
                        self.target = 0

                if pos_offset < 0:
                    if tick.last_price > self.snap_price:
                        self.write_log(f"Current price > snap price. Doing position offset(trades {pos_offset}). Total Pos : {self.current_pos}")
                        self.target = pos_offset
                    else:
                        self.write_log(f"Current price < snap price, not favor for SHORT. Offset(trades {pos_offset}) skipped.")
                        self.target_middle = pos_offset
                        self.target = 0

            else:
                self.write_log(f"Position unchanged. No further action.")


        self.put_event()

    def on_stop(self):
        """
        Callback when strategy is stopped.
        """
        self.cancel_all()
        self.write_log("策略停止")

    def on_tick(self, tick: TickData):
        """
        Callback of new tick data update.
        """
        self.pbg.update_tick(tick)
        if self.trading == False:
            return
        self.last_price = tick.last_price
        self.current_pos = self.get_pos(self.vt_symbol)

        ########## Check last price with anchor to determine new target if pos is 0 ##########
        if self.target_middle != 0:
            current_time = datetime.now().time()
            in_trading_period = self.time_in_trading_period(self.t_open_time, self.t_close_time, current_time)
            if self.trading_in_process == False and self.target == 0 and in_trading_period and self.snap_price != -1 and tick.bid_price_1 != -1:
                if self.target_middle > 0 and tick.last_price < self.snap_price:
                    self.write_log(f"Current price < snap price. Doing position trades {self.target_middle}.")
                    self.target = self.pos_offset
                    self.target_middle = 0

                if self.target_middle < 0 and tick.last_price > self.snap_price:
                    self.write_log(f"Current price > snap price. Doing position trades {self.target_middle}.")
                    self.target = self.pos_offset
                    self.target_middle = 0


        ########## Place order when new target ##########
        # self.write_log(tick)
        if self.target != 0 and tick.bid_price_1 != -1:
            if self.target > 0:
                price = tick.bid_price_1 + self.algo_limit_place
                volume = abs(self.target)
                self.vt_orderids = self.buy(self.vt_symbol, price, volume)
                self.vt_orderids_datetime = datetime.now()
                self.open_orderids.extend(self.vt_orderids)
                self.write_log(f"LONG {volume} limit order (id:{self.vt_orderids}) sent with bid({price}). (bid ask:{tick.bid_price_1} {tick.ask_price_1})")
            elif self.target < 0:
                price = tick.ask_price_1 - self.algo_limit_place
                volume = abs(self.target)
                self.vt_orderids = self.short(self.vt_symbol, price, volume)
                self.vt_orderids_datetime = datetime.now()
                self.open_orderids.extend(self.vt_orderids)
                self.write_log(f"SHORT {volume} limit order (id:{self.vt_orderids}) sent with ask({price}). (bid ask:{tick.bid_price_1} {tick.ask_price_1})")
            self.target = 0
            self.trading_in_process = True

        ########## Print when order traded ##########
        # open_orderids
        pending_order = False
        order_summary = ""
        for vt_orderid in self.open_orderids:
            order = self.get_order(vt_orderid)
            if order:
                if order.traded > 0:
                    if order.traded != self.prev_traded:
                        # self.write_log(f"{order}")
                        traded_offset = order.traded - self.prev_traded
                        self.write_log(f"Order (id:{vt_orderid}) with pos {traded_offset} traded. {order.direction} {order.traded}/{order.volume} {order.symbol} @ {order.price} ")
                        # if order.direction == Direction.LONG:
                        #     self.current_pos = self.current_pos + traded_offset
                        # elif order.direction == Direction.SHORT:
                        #     self.current_pos = self.current_pos - traded_offset
                        self.prev_traded = order.traded

                if order.status == Status.ALLTRADED:
                    self.write_log(f"Order (id:{vt_orderid}) all traded. {order.direction} {order.traded}/{order.volume} {order.symbol} @ {order.price} ")
                    self.trading_in_process = False
                    self.prev_traded = 0
                    self.open_orderids.remove(vt_orderid)
                if order.status == Status.PARTTRADED:
                    self.write_log(f"Order {vt_orderid} partially placed. {order.direction} {order.traded}/{order.volume} {order.symbol} @ {order.price}. ")
                if order.status == Status.CANCELLED or order.status == Status.REJECTED:
                    self.open_orderids.remove(vt_orderid)
                if order.status == Status.SUBMITTING or order.status == Status.PARTTRADED or order.status == Status.NOTTRADED:
                    pending_order = True

                order_summary = order_summary + f"{vt_orderid}<{order.status.value}>,"
        # if pending_order == False and self.trading_in_process:
        #     self.write_log(f"No pending order and continue the latest tick data checking.")
        #     self.trading_in_process = False


        ########## Cancel old order when exceed algo limit and place new order ##########
        # get_all_active_orderids
        all_active_orderids = self.get_all_active_orderids()
        fake_submitting = False
        no_active_orderids = True
        if all_active_orderids:
            order_finished = False
            for vt_orderid in all_active_orderids:  # vt_orderid = all_active_orderids[0] # for vt_orderid in all_active_orderids:
                if vt_orderid in self.fake_orderids:
                    continue
                no_active_orderids = False
                self.last_vt_orderid = vt_orderid
                order = self.get_order(vt_orderid)
                # self.write_log(f"{order}")
                redo_long = False
                redo_short = False

                if order:   #if order.status == Status.PARTTRADED or order.status == Status.NOTTRADED or order.status == Status.SUBMITTING:
                    if order.direction == Direction.LONG and (tick.bid_price_1 - order.price) > self.algo_limit_spread and self.chase_long_trigger == False and (order.volume - order.traded) > 0:
                        #cancel the original order and make a new one
                        redo_long = True
                        self.write_log(f"Cancel LONG order (id:{vt_orderid}) [Traded:{order.traded}/{order.volume}] - Algo limit spread threshold exceeded. Cancel and place a new order - bid:{tick.bid_price_1} price:{order.price} spread:{self.algo_limit_spread}")
                    if order.direction == Direction.SHORT and (order.price - tick.ask_price_1) > self.algo_limit_spread and self.chase_short_trigger == False and (order.volume - order.traded) > 0:
                        #cancel the original order and make a new one
                        redo_short = True
                        self.write_log(f"Cancel SHORT order (id:{vt_orderid}) [Traded:{order.traded}/{order.volume}] - Algo limit spread threshold exceeded. Cancel and place a new order - ask:{tick.ask_price_1} price:{order.price} spread:{self.algo_limit_spread}")

                    # self.write_log(f"{order}")
                    # self.write_log(f"{tick.datetime.replace(tzinfo=None)} - {self.vt_orderids_datetime}")
                    if order.direction == Direction.LONG and order.status == Status.SUBMITTING and order.traded == 0 and (tick.datetime.replace(tzinfo=None) - self.vt_orderids_datetime).total_seconds() > self.chase_interval:
                        #fake submitting on hold
                        fake_submitting = True
                        redo_long = True
                        self.fake_orderids.append(vt_orderid)
                        self.write_log(f"Fake SUBMITTING Order. Cancel LONG order (id:{vt_orderid}) [Traded:{order.traded}/{order.volume}]. Cancel and place a new order - bid:{tick.bid_price_1} price:{order.price} spread:{self.algo_limit_spread}")
                    if order.direction == Direction.SHORT and order.status == Status.SUBMITTING and order.traded == 0 and (tick.datetime.replace(tzinfo=None) - self.vt_orderids_datetime).total_seconds() > self.chase_interval:
                        #fake submitting on hold
                        fake_submitting = True
                        redo_short = True
                        self.fake_orderids.append(vt_orderid)
                        self.write_log(f"Fake SUBMITTING Order. Cancel SHORT order (id:{vt_orderid}) [Traded:{order.traded}/{order.volume}]. Cancel and place a new order - bid:{tick.ask_price_1} price:{order.price} spread:{self.algo_limit_spread}")

                    if redo_long:
                        self.write_log(f"{order}")
                        price = tick.bid_price_1 + self.algo_limit_place
                        untrade_volume = order.volume - order.traded
                        self.long_trade_volume = untrade_volume
                        self.cancel_order(vt_orderid)
                        self.chase_long_trigger = True
                    if redo_short:
                        self.write_log(f"{order}")
                        price = tick.ask_price_1 - self.algo_limit_place
                        untrade_volume = order.volume - order.traded
                        self.short_trade_volume = untrade_volume
                        self.cancel_order(vt_orderid)
                        self.chase_short_trigger = True
        if no_active_orderids:
            order_finished = True
            self.cancel_status = False

        order = self.get_order(self.last_vt_orderid)
        if order is not None:
            if (order.status == Status.CANCELLED or order.status == Status.REJECTED) or fake_submitting:

                if self.chase_long_trigger == False and self.chase_short_trigger == False:
                    # Got automatic cancel
                    order = self.get_order(self.last_vt_orderid)
                    if order.direction == Direction.LONG:
                        #cancel the original order and make a new one
                        self.write_log(f"{order}")
                        price = tick.bid_price_1 + self.algo_limit_place
                        untrade_volume = order.volume - order.traded
                        self.long_trade_volume = untrade_volume
                        self.chase_long_trigger = True
                        self.write_log(f"Got cancel LONG order (id:{self.last_vt_orderid}) [Traded:{order.traded}/{order.volume}]. Place a new order - bid:{tick.bid_price_1} price:{order.price} spread:{self.algo_limit_spread}")

                    if order.direction == Direction.SHORT:
                        #cancel the original order and make a new one
                        self.write_log(f"{order}")
                        price = tick.ask_price_1 - self.algo_limit_place
                        untrade_volume = order.volume - order.traded
                        self.short_trade_volume = untrade_volume
                        self.chase_short_trigger = True
                        self.write_log(f"Got cancel SHORT order (id:{self.last_vt_orderid}) [Traded:{order.traded}/{order.volume}]. Place a new order - ask:{tick.ask_price_1} price:{order.price} spread:{self.algo_limit_spread}")


                if self.chase_long_trigger:
                    if order_finished or fake_submitting:
                        price = tick.bid_price_1 + self.algo_limit_place
                        self.vt_orderids = self.buy(self.vt_symbol, price, self.long_trade_volume)
                        self.vt_orderids_datetime = datetime.now()
                        self.open_orderids.extend(self.vt_orderids)
                        self.write_log(f"New LONG {self.long_trade_volume} limit order (id:{self.vt_orderids}) sent with bid({price}). (bid ask:{tick.bid_price_1} {tick.ask_price_1})")
                        self.long_trade_volume = 0
                        self.chase_long_trigger = False
                        self.prev_traded = 0
                    else:
                        self.cancel_surplus_order(all_active_orderids)
                elif self.chase_short_trigger:
                    if order_finished or fake_submitting:
                        price = tick.ask_price_1 - self.algo_limit_place
                        self.vt_orderids = self.short(self.vt_symbol, price, self.short_trade_volume)
                        self.vt_orderids_datetime = datetime.now()
                        self.open_orderids.extend(self.vt_orderids)
                        self.write_log(f"New SHORT {self.short_trade_volume} limit order (id:{self.vt_orderids}) sent with ask({price}). (bid ask:{tick.bid_price_1} {tick.ask_price_1})")
                        self.short_trade_volume = 0
                        self.chase_short_trigger = False
                        self.prev_traded = 0
                    else:
                        self.cancel_surplus_order(all_active_orderids)



        if self.trading:
            self.debug_message = (f"Debug:Trading<{self.trading_in_process}>,Target<{self.target}>,CurrPos<{self.current_pos}>,order_finished<{order_finished}>, " +
                       f"cancel_status<{self.cancel_status}>,chase_long_trigger<{self.chase_long_trigger}>,chase_short_trigger<{self.chase_short_trigger}>, " +
                       f"OpenOrder<{self.open_orderids}>,<{self.get_all_active_orderids()}>,order_summary[{order_summary}], fake_orderids[{self.fake_orderids}]")
            if self.prev_debug_message != self.debug_message:
                self.write_log(self.debug_message)
                self.prev_debug_message = self.debug_message
        self.put_event()

    def on_bars(self, bars: Dict[str, BarData]):
        """
        Callback of new bars data update.
        """
        self.pbg.update_bars(bars)

    def on_30min_bar(self, bars: Dict[str, BarData]):
        """"""
        self.cancel_all()
        # self.write_log(bars)

        for vt_symbol, bar in bars.items():
            # open order only at 10:30am
            if bar.datetime.hour == self.bar_open_time.hour and bar.datetime.minute == self.bar_open_time.minute:   #on bar close, so the bar of 10:00 means 10:30
                am: ArrayManager = self.ams[vt_symbol]
                am.update_bar(bar)
                # tick = self.pbg.last_ticks[vt_symbol]
                self.write_log(f"{bar.datetime} : {bar.close_price}, count : {am.count}")

                if am.count >= self.window:
                    # sma_array = am.sma(self.window, array=True)
                    # self.write_log(sma_array)
                    # std_array = am.std(self.window, array=True)
                    # self.write_log(std_array)

                    if bar.datetime.date() == datetime.today().date() - timedelta(days=0):  # Developemt+Production (order checking in the middle at the same day)
                    # if self.trading:   # Production (no order checking in the middle)
                        self.snap_price = bar.close_price
                        # self.z_score = (bar.close_price - sma_array[-1]) / std_array[-1]
                        # self.write_log(f"(bar.close_price - sma_array[-1]) / std_array[-1] -> ({bar.close_price} - {sma_array[-1]}) / {std_array[-1]} = {self.z_score}")

                        close_array = am.close_array
                        # self.write_log(f"{close_array}")
                        # self.write_log(f"{close_array[-self.window:]}")
                        New_MA = np.mean(close_array[-self.window:])
                        New_STD = np.std(close_array[-self.window:], ddof=1)
                        New_Zscore = float((close_array[-1] - New_MA) / New_STD)
                        self.write_log(f"z_score : (bar.close_price - sma_array[-1]) / std_array[-1] -> ({close_array[-1]} - {New_MA}) / {New_STD} = {New_Zscore}")
                        self.z_score = New_Zscore

                        if self.z_score < self.thre:
                            self.target = self.fixed_size
                            self.write_log(f"z_score({self.z_score}) < {self.thre}. Begin LONG {abs(self.target)} pos.")
                        elif self.z_score > self.thre:
                            self.target = -self.fixed_size
                            self.write_log(f"z_score({self.z_score}) > {self.thre}. Begin SHORT {abs(self.target)} pos.")
                        else:
                            self.target = 0
                        self.target_today = self.target

            if bar.datetime.hour == self.bar_close_time.hour and bar.datetime.minute == self.bar_close_time.minute:  # on bar close, so the bar of 22:00 means 22:30
                if self.current_pos > 0:
                    self.target = -self.current_pos
                    self.write_log(f"Close all pos at {self.close_time}. Begin SHORT {abs(self.current_pos)} pos.")
                elif self.current_pos < 0:
                    self.target = -self.current_pos
                    self.write_log(f"Close all pos at {self.close_time}. Begin LONG {abs(self.current_pos)} pos.")
                self.target_today = 0
                self.target_middle = 0

        self.z_score_latest = self.z_score
        self.target_today_latest = self.target_today
        self.target_middle_latest = self.target_middle
        self.snap_price_latest = self.snap_price

        self.put_event()

    def time_in_trading_period(self, start, end, current):    #working only with 10:30 to 22:30
        """Returns whether current is in the range [start, end]"""
        return start <= current and current <= end

    def time_in_close_position_period(self, start, end, current):
        """Returns whether current is in the range [start, end]"""
        return start > current or current > end

    def cancel_surplus_order(self,orderids:list):
        """
        撤销剩余活动委托单
        """
        if not self.cancel_status:
            for vt_orderid in orderids:
                self.cancel_order(vt_orderid)
                self.write_log(f"cancel_surplus_order:{vt_orderid}")
            self.cancel_status = True