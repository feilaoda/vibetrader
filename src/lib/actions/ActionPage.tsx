import React, { useState } from 'react';
import { ActionWatchlist } from './ActionWatchlist';
import { ActionTimeline } from './ActionTimeline';
import '../styles/ActionPage.css';

interface ActionPageProps {
    onBack: () => void;
}

export const ActionPage: React.FC<ActionPageProps> = ({ onBack }) => {
    const [selectedSymbol, setSelectedSymbol] = useState<string | undefined>(() => {
        return localStorage.getItem('last_selected_symbol') || undefined;
    });

    const handleSelect = (symbol: string) => {
        setSelectedSymbol(symbol);
        localStorage.setItem('last_selected_symbol', symbol);
        // Dispatch custom event for same-tab synchronization
        window.dispatchEvent(new CustomEvent('vibetrader-symbol-change', { detail: { symbol } }));
    };

    return (
        <div className="action-page">
            {/* Header */}
            <header className="action-header">
                <button className="back-btn" onClick={onBack} title="Back to Chart">
                    &lt; Back to Chart
                </button>
                <div className="title">Action Plan Manager</div>
            </header>

            {/* Main Content */}
            <div className="action-content">
                <ActionWatchlist selectedSymbol={selectedSymbol} onSelect={handleSelect} />
                <ActionTimeline symbol={selectedSymbol} />
            </div>
        </div>
    );
};
